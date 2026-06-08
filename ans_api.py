"""
Bigdoc — Intégration API Annuaire Santé (ANS)
Enrichit les fiches leads avec les données RPPS officielles.

Documentation : https://gateway.api.esante.gouv.fr/fhir/v1/
"""
import os
import logging

logger = logging.getLogger("bigdoc")

ANS_API_KEY = os.getenv("ANS_API_KEY", "")
ANS_BASE    = "https://gateway.api.esante.gouv.fr/fhir/v1"


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
            # Practitioner par RPPS
            r = await client.get(
                f"{ANS_BASE}/Practitioner",
                params={"identifier": f"http://rpps.fr|{rpps}", "_format": "json"},
                headers=_headers()
            )
            if r.status_code != 200:
                logger.warning(f"ANS RPPS {rpps}: HTTP {r.status_code}")
                return None
            data = r.json()
            entries = data.get("entry", [])
            if not entries:
                return None
            practitioner = entries[0]["resource"]

            # PractitionerRole pour spécialité + adresse + secteur
            rpps_id = practitioner.get("id", "")
            role_data = {}
            if rpps_id:
                r2 = await client.get(
                    f"{ANS_BASE}/PractitionerRole",
                    params={"practitioner": rpps_id, "_format": "json", "_count": "5"},
                    headers=_headers()
                )
                if r2.status_code == 200:
                    role_entries = r2.json().get("entry", [])
                    if role_entries:
                        role_data = role_entries[0]["resource"]

            return _parse_practitioner(practitioner, role_data)
    except Exception as e:
        logger.warning(f"ANS API error: {e}")
        return None


async def search_by_name(prenom: str, nom: str, specialite: str = "", ville: str = "") -> list[dict]:
    """Recherche des praticiens par nom et prénom, avec filtre géographique optionnel."""
    if not ANS_API_KEY or not nom:
        return []
    try:
        import httpx, re
        params = {
            "family": nom.strip(),
            "_format": "json",
            "_count": "10"
        }
        if prenom:
            params["given"] = prenom.strip()

        # Filtre géographique via code postal
        if ville:
            m = re.search(r'\((\d{5})\)', ville)
            if m:
                params["address-postalcode"] = m.group(1)

        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                f"{ANS_BASE}/Practitioner",
                params=params,
                headers=_headers()
            )
            if r.status_code != 200:
                logger.warning(f"ANS search {nom}: HTTP {r.status_code}")
                return []
            entries = r.json().get("entry", [])
            results = []
            for entry in entries[:10]:
                p = entry["resource"]
                parsed = _parse_practitioner(p, {})
                if parsed:
                    results.append(parsed)
            return results
    except Exception as e:
        logger.warning(f"ANS name search error: {e}")
        return []


def _parse_practitioner(p: dict, role: dict) -> dict:
    """Parse un Practitioner FHIR en dict lisible."""
    result = {}

    # RPPS
    for ident in p.get("identifier", []):
        if "rpps" in ident.get("system", "").lower():
            result["rpps"] = ident.get("value", "")

    # Nom + Prénom
    for name in p.get("name", []):
        if name.get("use") == "official" or not result.get("nom"):
            result["nom"]    = " ".join(name.get("family", []) if isinstance(name.get("family"), list) else [name.get("family", "")])
            result["prenom"] = " ".join(name.get("given", []))

    # Spécialité depuis qualification
    specs = []
    for qual in p.get("qualification", []):
        code = qual.get("code", {})
        for coding in code.get("coding", []):
            display = coding.get("display", "")
            if display and display not in specs:
                specs.append(display)
    if specs:
        result["specialite_ans"] = specs[0]
        result["qualifications"] = specs

    # Depuis PractitionerRole
    if role:
        # Spécialité
        for spec in role.get("specialty", []):
            for coding in spec.get("coding", []):
                display = coding.get("display", "")
                if display and not result.get("specialite_ans"):
                    result["specialite_ans"] = display

        # Adresse
        for loc_ref in role.get("location", []):
            result["location_ref"] = loc_ref.get("reference", "")

        # Mode exercice et secteur
        for ext in role.get("extension", []):
            url = ext.get("url", "")
            if "modeExercice" in url:
                result["mode_exercice"] = ext.get("valueCodeableConcept", {}).get("coding", [{}])[0].get("display", "")
            if "secteurConventionnement" in url or "conventionnement" in url.lower():
                result["secteur_conventionnel"] = ext.get("valueCodeableConcept", {}).get("coding", [{}])[0].get("display", "")

        # Téléphone / Email pro
        for telecom in role.get("telecom", []):
            system = telecom.get("system", "")
            value  = telecom.get("value", "")
            if system == "phone" and not result.get("telephone"):
                result["telephone"] = value
            if system == "email" and not result.get("email_pro"):
                result["email_pro"] = value

        # Adresse cabinet
        for addr in role.get("address", []):
            result["adresse"] = " ".join(filter(None, [
                " ".join(addr.get("line", [])),
                addr.get("postalCode", ""),
                addr.get("city", "")
            ]))

    return result if result.get("rpps") or result.get("nom") else None
