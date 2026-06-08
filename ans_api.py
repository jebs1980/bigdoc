"""
Bigdoc — Intégration API Annuaire Santé (ANS)
Enrichit les fiches leads avec les données RPPS officielles.

Documentation : https://gateway.api.esante.gouv.fr/fhir/v1/
"""
import os
import logging

logger = logging.getLogger("bigdoc")

ANS_API_KEY = os.getenv("ANS_API_KEY", "")
ANS_BASE_V1 = "https://gateway.api.esante.gouv.fr/fhir/v1"
ANS_BASE    = "https://gateway.api.esante.gouv.fr/fhir/v2"


def _headers():
    return {
        "ESANTE-API-KEY": ANS_API_KEY,
        "Accept": "application/json",
    }


async def search_by_rpps(rpps: str) -> dict | None:
    """Recherche un praticien par numéro RPPS."""
    if not ANS_API_KEY or not rpps:
        return None
    rpps = rpps.strip().replace(" ", "")
    if not rpps.isdigit() or len(rpps) not in (10, 11):
        return None
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Essayer v2 d'abord (meilleur coverage adresses), fallback v1
            practitioner = None
            pract_id = ""
            for base in [ANS_BASE, ANS_BASE_V1]:
                r = await client.get(
                    f"{base}/Practitioner",
                    params={"identifier": f"http://rpps.fr|{rpps}", "_format": "json"},
                    headers=_headers()
                )
                if r.status_code == 200:
                    entries = r.json().get("entry", [])
                    if entries:
                        practitioner = entries[0]["resource"]
                        pract_id = practitioner.get("id", "")
                        break

            if not practitioner:
                logger.warning(f"ANS RPPS {rpps}: non trouvé v1/v2")
                return None

            # Récupérer PractitionerRole — essayer v2 puis v1
            role_data = {}
            if pract_id:
                for base in [ANS_BASE, ANS_BASE_V1]:
                    r2 = await client.get(
                        f"{base}/PractitionerRole",
                        params={"practitioner": pract_id, "_format": "json", "_count": "5"},
                        headers=_headers()
                    )
                    if r2.status_code == 200:
                        role_entries = r2.json().get("entry", [])
                        if role_entries:
                            role_data = role_entries[0]["resource"]
                            # Si on a une adresse, on s'arrête
                            if role_data.get("address") or any(
                                addr.get("city") for addr in role_data.get("address", [])
                            ):
                                break

            return _parse_practitioner(practitioner, role_data)
    except Exception as e:
        logger.warning(f"ANS API error: {e}")
        return None


async def search_by_name(prenom: str, nom: str, specialite: str = "", ville: str = "") -> list[dict]:
    """Recherche des praticiens par nom et prénom via API v2."""
    if not ANS_API_KEY or not nom:
        return []
    try:
        import httpx
        params = {
            "family": nom.strip(),
            "_format": "json",
            "_count": "50",
            "_total": "accurate"
        }
        if prenom:
            params["given"] = prenom.strip()

        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                f"{ANS_BASE}/Practitioner",
                params=params,
                headers=_headers()
            )
            if r.status_code != 200:
                logger.warning(f"ANS search {nom}: HTTP {r.status_code}")
                return []
            data = r.json()
            total = data.get("total", 0)
            entries = data.get("entry", [])

            # Si trop de résultats et pas de prénom → signal pour demander affinement
            if total > 50 and not prenom:
                return [{"__too_many__": True, "__total__": total, "__nom__": nom}]

            # Parser les praticiens de base d'abord
            base_results = []
            for entry in entries:
                p = entry.get("resource", {})
                parsed = _parse_practitioner(p, {})
                if parsed:
                    # Stocker l'ID FHIR pour enrichissement
                    parsed["_fhir_id"] = p.get("id", "")
                    base_results.append(parsed)

            # Enrichir en parallèle avec PractitionerRole (adresse + spécialité + secteur)
            import asyncio
            async def enrich_one(client, result):
                fhir_id = result.pop("_fhir_id", "")
                if not fhir_id:
                    return result
                try:
                    r2 = await client.get(
                        f"{ANS_BASE_V1}/PractitionerRole",
                        params={"practitioner": fhir_id, "_format": "json", "_count": "5"},
                        headers=_headers(),
                        timeout=3.0
                    )
                    if r2.status_code == 200:
                        role_entries = r2.json().get("entry", [])
                        if role_entries:
                            role_data = role_entries[0]["resource"]
                            # Extraire spécialité, adresse, secteur depuis le rôle
                            for spec in role_data.get("specialty", []):
                                for coding in spec.get("coding", []):
                                    display = coding.get("display", "")
                                    if display and not result.get("specialite_ans"):
                                        result["specialite_ans"] = display
                            for ext in role_data.get("extension", []):
                                url = ext.get("url", "")
                                val = ext.get("valueCodeableConcept", {})
                                codings = val.get("coding", [{}])
                                display = codings[0].get("display", "") if codings else ""
                                if "conventionnement" in url.lower() and display:
                                    result["secteur_conventionnel"] = display
                            for addr in role_data.get("address", []):
                                parts = [
                                    " ".join(addr.get("line", [])),
                                    addr.get("postalCode", ""),
                                    addr.get("city", "")
                                ]
                                result["adresse"] = " ".join(filter(None, parts)).strip()
                                if addr.get("city"):
                                    result["ville"] = addr["city"]
                except Exception:
                    pass  # Timeout ou erreur — on garde le résultat de base
                return result

            async with httpx.AsyncClient(timeout=15.0) as enrich_client:
                tasks = [enrich_one(enrich_client, r) for r in base_results]
                results = await asyncio.gather(*tasks)

            return list(results)
    except Exception as e:
        logger.warning(f"ANS name search error: {e}")
        return []


def _parse_practitioner(p: dict, role: dict) -> dict:
    """Parse un Practitioner FHIR en dict lisible."""
    result = {}

    # RPPS — cherche dans les identifiers
    for ident in p.get("identifier", []):
        system = ident.get("system", "")
        if "rpps" in system.lower() or "1.2.250.1.71.4.2.1" in system:
            val = ident.get("value", "")
            if val and val.isdigit():
                result["rpps"] = val

    # Nom + Prénom — cherche le nom officiel
    for name in p.get("name", []):
        use = name.get("use", "")
        if use in ("official", "usual") or not result.get("nom"):
            family = name.get("family", "")
            given  = name.get("given", [])
            if isinstance(family, list):
                family = " ".join(family)
            if family:
                result["nom"]    = family.strip()
                result["prenom"] = " ".join(given).strip() if given else ""

    # Qualification — DIPLÔMES uniquement, PAS la spécialité
    diplomes = []
    for qual in p.get("qualification", []):
        code = qual.get("code", {})
        for coding in code.get("coding", []):
            display = coding.get("display", "")
            # Filtrer les vrais diplômes vs les spécialités
            if display and "Diplôme" not in display and "diplôme" not in display:
                diplomes.append(display)
    if diplomes:
        result["qualifications"] = diplomes

    # Depuis PractitionerRole — spécialité + adresse + secteur
    if role:
        # Spécialité réelle
        for spec in role.get("specialty", []):
            for coding in spec.get("coding", []):
                display = coding.get("display", "")
                if display and not result.get("specialite_ans"):
                    result["specialite_ans"] = display

        # Mode exercice et secteur conventionnel
        for ext in role.get("extension", []):
            url = ext.get("url", "")
            val = ext.get("valueCodeableConcept", {})
            codings = val.get("coding", [{}])
            display = codings[0].get("display", "") if codings else ""
            if "modeExercice" in url and display:
                result["mode_exercice"] = display
            if "conventionnement" in url.lower() and display:
                result["secteur_conventionnel"] = display

        # Téléphone / Email
        for telecom in role.get("telecom", []):
            system = telecom.get("system", "")
            value  = telecom.get("value", "")
            if system == "phone" and not result.get("telephone"):
                result["telephone"] = value
            if system == "email" and not result.get("email_pro"):
                result["email_pro"] = value

        # Adresse cabinet
        for addr in role.get("address", []):
            parts = [
                " ".join(addr.get("line", [])),
                addr.get("postalCode", ""),
                addr.get("city", "")
            ]
            result["adresse"] = " ".join(filter(None, parts)).strip()

    # Fallback spécialité — extraire depuis qualifications si specialite_ans absent
    if not result.get("specialite_ans") and result.get("qualifications"):
        # Chercher un CES ou DES ou spécialité reconnue
        spe_keywords = ["Gynécologie", "Médecine générale", "Pédiatrie", "Psychiatrie",
                        "Dermatologie", "Cardiologie", "Chirurgie", "Ophtalmologie",
                        "Rhumatologie", "Neurologie", "Oncologie", "Anesthésie",
                        "Radiologie", "Gastro", "Pneumologie", "Endocrinologie",
                        "Néphrologie", "Urologie", "ORL", "Stomatologie"]
        for q in result["qualifications"]:
            for kw in spe_keywords:
                if kw.lower() in q.lower():
                    result["specialite_ans"] = q.replace("CES ", "").replace("DES ", "").replace("DESC ", "").strip()
                    break
            if result.get("specialite_ans"):
                break

    return result if result.get("rpps") or result.get("nom") else None
