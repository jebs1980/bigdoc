"""
Bigdoc — Intégration Data Ameli Open Data
Enrichit le contexte géographique avec des données locales réelles.

Données disponibles par spécialité + département :
- File active moyenne (patients vus/an)
- Honoraires moyens annuels
- Effectifs par secteur conventionnel (S1/S2/S3)
"""
import json
import time
import logging
from pathlib import Path

logger = logging.getLogger("bigdoc")

AMELI_BASE = "https://data.ameli.fr/api/explore/v2.1/catalog/datasets"

# Cache simple en mémoire — (dataset, dept, spe) → (data, timestamp)
_cache = {}
CACHE_TTL = 86400  # 24h

# Correspondance spécialités Bigdoc → libellés Ameli
SPECIALITE_MAP = {
    "médecine générale":          "Médecine générale",
    "généraliste":                "Médecine générale",
    "pédiatrie":                  "Pédiatrie",
    "gynécologie":                "Gynécologie-obstétrique",
    "gynécologie médicale":       "Gynécologie médicale",
    "gynécologie-obstétrique":    "Gynécologie-obstétrique",
    "psychiatrie":                "Psychiatrie",
    "cardiologie":                "Cardiologie",
    "dermatologie":               "Dermatologie",
    "ophtalmologie":              "Ophtalmologie",
    "neurologie":                 "Neurologie",
    "gastro-entérologie":         "Gastro-entérologie et hépatologie",
    "pneumologie":                "Pneumologie",
    "rhumatologie":               "Rhumatologie",
    "endocrinologie":             "Endocrinologie-métabolisme",
    "néphrologie":                "Néphrologie",
    "urologie":                   "Urologie",
    "orl":                        "Oto-rhino-laryngologie",
    "chirurgie générale":         "Chirurgie générale",
    "chirurgie orthopédique":     "Chirurgie orthopédique et traumatologie",
    "chirurgie vasculaire":       "Chirurgie vasculaire",
    "anesthésie":                 "Anesthésie-réanimation",
    "radiologie":                 "Radiodiagnostic et imagerie médicale",
    "médecine interne":           "Médecine interne",
    "gériatrie":                  "Gériatrie",
    "hématologie":                "Hématologie",
    "oncologie":                  "Oncologie médicale",
    "infectiologie":              "Maladies infectieuses et tropicales",
    "allergologie":               "Pathologie cardiovasculaire",
    "médecine du travail":        "Médecine du travail",
    "médecine d'urgence":         "Médecine générale",
}


def _normalize_spe(spe: str) -> str | None:
    """Normalise une spécialité vers le libellé Ameli."""
    if not spe:
        return None
    s = spe.lower().strip()
    for key, val in SPECIALITE_MAP.items():
        if key in s or s in key:
            return val
    return None


def _extract_dept(ville: str) -> str | None:
    """Extrait le code département depuis la ville. Ex: 'Lyon 6e (69006)' → '69'"""
    if not ville:
        return None
    import re
    # Cherche un code postal 5 chiffres entre parenthèses
    m = re.search(r'\((\d{5})\)', ville)
    if m:
        cp = m.group(1)
        if cp.startswith("97"):
            return cp[:3]  # DOM: 971, 972, etc.
        return cp[:2]
    # Cherche un code à 2 chiffres direct
    m2 = re.search(r'\b(\d{2})\b', ville)
    if m2:
        return m2.group(1)
    return None


async def _fetch_ameli(dataset: str, dept: str, spe_ameli: str, select: str) -> dict | None:
    """Appelle l'API Data Ameli avec cache."""
    cache_key = f"{dataset}:{dept}:{spe_ameli}"
    if cache_key in _cache:
        data, ts = _cache[cache_key]
        if time.time() - ts < CACHE_TTL:
            return data

    try:
        import httpx
        url = f"{AMELI_BASE}/{dataset}/records"
        params = {
            "where": f'niveau_territorial="Departement" AND code_departement="{dept}" AND libelle_profession="{spe_ameli}"',
            "select": select,
            "order_by": "annee DESC",
            "limit": 1,
            "lang": "fr"
        }
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(url, params=params)
            if r.status_code == 200:
                data = r.json()
                result = data.get("results", [{}])[0] if data.get("results") else None
                _cache[cache_key] = (result, time.time())
                return result
            else:
                logger.warning(f"Ameli API {dataset}: HTTP {r.status_code}")
    except Exception as e:
        logger.warning(f"Ameli API error ({dataset}): {e}")
    return None


async def get_ameli_context(specialite: str, ville: str) -> str:
    """
    Retourne un bloc de contexte local enrichi avec données Ameli.
    À injecter dans le prompt de diagnostic.
    """
    spe_ameli = _normalize_spe(specialite)
    dept = _extract_dept(ville)

    if not spe_ameli or not dept:
        return ""

    lines = []
    annee_ref = None

    # ── File active
    fa = await _fetch_ameli(
        "patientele", dept, spe_ameli,
        "annee,file_active_moyenne"
    )
    if fa and fa.get("file_active_moyenne") and fa["file_active_moyenne"] not in ("NS", "NC", None):
        try:
            val = int(float(str(fa["file_active_moyenne"]).replace(" ", "")))
            annee_ref = fa.get("annee", "2024")
            lines.append(f"File active moyenne {spe_ameli} — département {dept} : {val:,} patients/an (Ameli {annee_ref})".replace(",", " "))
        except Exception:
            pass

    # ── Honoraires
    hon = await _fetch_ameli(
        "honoraires", dept, spe_ameli,
        "annee,honoraires_moyens_annuels"
    )
    if hon and hon.get("honoraires_moyens_annuels") and hon["honoraires_moyens_annuels"] not in ("NS", "NC", None):
        try:
            val = int(float(str(hon["honoraires_moyens_annuels"]).replace(" ", "")))
            annee = hon.get("annee", annee_ref or "2024")
            lines.append(f"Honoraires moyens annuels {spe_ameli} — département {dept} : {val:,} € (Ameli {annee})".replace(",", " "))
        except Exception:
            pass

    # ── Secteurs conventionnels
    sec = await _fetch_ameli(
        "demographie-secteurs-conventionnels", dept, spe_ameli,
        "annee,part_secteur1,part_secteur2,part_secteur3,effectif_total"
    )
    if sec and sec.get("effectif_total") and sec["effectif_total"] not in ("NS", "NC", None):
        try:
            annee = sec.get("annee", annee_ref or "2024")
            effectif = sec["effectif_total"]
            parts = []
            if sec.get("part_secteur1") not in (None, "NC", "NS"):
                parts.append(f"S1 : {sec['part_secteur1']}%")
            if sec.get("part_secteur2") not in (None, "NC", "NS"):
                parts.append(f"S2/OPTAM : {sec['part_secteur2']}%")
            if sec.get("part_secteur3") not in (None, "NC", "NS"):
                parts.append(f"S3 : {sec['part_secteur3']}%")
            if parts:
                lines.append(f"Répartition sectorielle {spe_ameli} — département {dept} ({effectif} médecins) : {' / '.join(parts)} (Ameli {annee})")
        except Exception:
            pass

    if not lines:
        return ""

    header = f"\nDONNÉES AMELI LOCALES — {spe_ameli.upper()} — DÉPARTEMENT {dept} :"
    return header + "\n" + "\n".join(f"  → {l}" for l in lines) + "\n"


# Fonction synchrone pour usage non-async (fallback)
def get_ameli_context_sync(specialite: str, ville: str) -> str:
    """Version synchrone — lance une boucle event loop temporaire."""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Dans un contexte async — retourner vide, utiliser la version async
            return ""
        return loop.run_until_complete(get_ameli_context(specialite, ville))
    except Exception:
        return ""
