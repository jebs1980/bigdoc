import json
import time
from pathlib import Path
from datetime import datetime
import secrets
import httpx
import os
import logging
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
import anthropic

from config import (
    ANTHROPIC_API_KEY, STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET,
    TURNSTILE_SECRET, SYSTEM_PROMPT, SYSTEM_PROMPT_CHAT_REACTION,
    QUESTIONNAIRE, RESEND_API_KEY, FROM_EMAIL, ALERT_EMAIL
)
from database import (
    init_db, save_diagnostic, save_lead, get_diagnostic,
    create_partage_token, get_diagnostic_by_token,
    delete_lead_data, get_stats, get_all_leads,
    get_lead_by_session, verify_admin, init_admin,
    is_admin_setup_done, setup_admin,
    create_admin_session, verify_admin_session,
    delete_admin_session, create_reset_token, reset_admin_password,
    get_app_settings, save_app_settings,
    init_products, get_products, get_product,
    create_product, update_product, delete_product,
    toggle_product, get_catalogue_for_prompt,
    get_lead_fiche, update_lead_status, add_lead_note,
    delete_lead_event, update_lead_info, LEAD_STATUTS
)

# Modèle Anthropic — configurable dans .env
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")

logger = logging.getLogger("bigdoc")
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

# ─────────────────────────────────────────
# DONNÉES DÉMOGRAPHIQUES
# ─────────────────────────────────────────
DEMOGRAPHICS = {}

def load_demographics():
    """Charge le fichier demographics.json au démarrage."""
    global DEMOGRAPHICS
    demo_path = os.path.join(os.path.dirname(__file__), "data", "demographics.json")
    if os.path.exists(demo_path):
        with open(demo_path, "r", encoding="utf-8") as f:
            DEMOGRAPHICS = json.load(f)
        logger.info("✅ Données démographiques chargées")
    else:
        logger.warning("⚠️  data/demographics.json introuvable — contexte démographique désactivé")


def get_dept_from_ville(ville: str) -> str | None:
    """Extrait le code département depuis une ville formatée (ex: 'Lyon 3e (69003)' → '69')."""
    if not ville:
        return None
    import re
    # Chercher un code postal dans la ville (format: 75001, 69003, etc.)
    match = re.search(r'\((\d{5})\)', ville)
    if match:
        cp = match.group(1)
        # Paris → 75, Lyon → 69, Marseille → 13
        if cp.startswith('75'): return '75'
        if cp.startswith('69'): return '69'
        if cp.startswith('13'): return '13'
        return cp[:2]
    # Sinon chercher juste un CP 5 chiffres dans la chaîne
    match = re.search(r'\b(\d{5})\b', ville)
    if match:
        return match.group(1)[:2]
    return None


def normalize_specialite(specialite: str) -> str | None:
    """Normalise la spécialité pour matcher les clés du JSON."""
    if not specialite:
        return None
    spe = specialite.lower()
    if 'généraliste' in spe or 'médecine générale' in spe or 'generaliste' in spe:
        return 'Médecine générale'
    if 'gynéco' in spe or 'gynecologue' in spe:
        if 'médical' in spe or 'médicale' in spe:
            return 'Gynécologie médicale'
        return 'Gynécologie-obstétrique'
    if 'cardio' in spe:
        return 'Cardiologie'
    if 'pédiat' in spe or 'pediat' in spe:
        return 'Pédiatrie'
    if 'psychiatr' in spe:
        return 'Psychiatrie'
    if 'dermato' in spe:
        return 'Dermatologie'
    if 'ophtalmo' in spe or 'ophtalmologie' in spe:
        return 'Ophtalmologie'
    if 'orthopédie' in spe or 'orthopéd' in spe or 'chirurgie orthop' in spe:
        return 'Orthopédie'
    if 'gastro' in spe:
        return 'Gastro-entérologie'
    if 'pneumo' in spe:
        return 'Pneumologie'
    if 'neurolog' in spe:
        return 'Neurologie'
    if 'rhumato' in spe:
        return 'Rhumatologie'
    if 'endocrino' in spe:
        return 'Endocrinologie'
    if 'urolog' in spe:
        return 'Urologie'
    if 'orl' in spe or 'oto-rhino' in spe:
        return 'ORL'
    return None


def get_demographic_context(specialite: str, ville: str) -> str:
    """Génère le contexte démographique enrichi à injecter dans le prompt."""
    if not DEMOGRAPHICS:
        return ""

    spe_norm = normalize_specialite(specialite)
    dept = get_dept_from_ville(ville)
    lines = []

    # ── Densité locale vs nationale ──
    densite_nationale = None
    if spe_norm and spe_norm in DEMOGRAPHICS.get("densites_nationales", {}):
        densite_nationale = DEMOGRAPHICS["densites_nationales"][spe_norm]

    densite_locale = None
    dept_info = None
    if dept and dept in DEMOGRAPHICS.get("departements", {}):
        dept_info = DEMOGRAPHICS["departements"][dept]
        if spe_norm and spe_norm in dept_info.get("densites", {}):
            densite_locale = dept_info["densites"][spe_norm]

    if dept_info or densite_nationale:
        lines.append("═══ CONTEXTE DÉMOGRAPHIQUE ET ÉPIDÉMIOLOGIQUE ═══")

    if dept_info:
        dept_type = dept_info.get("type", "intermédiaire")
        dept_nom = dept_info.get("nom", dept)
        type_labels = {
            "sous_dote": "Zone avec besoins non couverts (ZIP ARS)",
            "densite_elevee": "Zone à densité médicale élevée",
            "sur_dote": "Zone à densité médicale élevée",  # legacy compat
            "intermédiaire": "Zone intermédiaire"
        }
        lines.append(f"Territoire : {dept_nom} ({dept}) — {type_labels.get(dept_type, dept_type)}")
        if dept_type == "sous_dote":
            lines.append("→ Zone sous-dotée ARS : aides CAIM jusqu'à 50 000€, DAC, CPTS — patientèle garantie dès l'ouverture. Valoriser dans le bilan.")
        elif dept_type in ("densite_elevee", "sur_dote"):
            ville_propre = ville.split('(')[0].strip().split(' ')[0] if ville else "cette zone"
            delais = DEMOGRAPHICS.get("donnees_drees_etat", {}).get("acces_soins", {}).get("variation_specialites", {})
            delai_spe = None
            for key, val in delais.items():
                if spe_norm and (key.lower() in spe_norm.lower() or spe_norm.lower() in key.lower()):
                    delai_spe = val
                    break
            delai_txt = f"{delai_spe} jours" if delai_spe else "49 jours"
            spe_label = spe_norm if spe_norm else "spécialiste"
            lines.append(f"→ Zone dite surdotée — mais cette appellation est trompeuse :")
            lines.append(f"  • Délai national moyen pour un RDV {spe_label} : {delai_txt} (Ameli / Doctolib 2025 — moyenne nationale)")
            lines.append(f"  • Dans les grandes villes comme {ville_propre}, les délais sont généralement plus courts qu'en zone rurale — mais restent significatifs : les créneaux sont pris et les besoins persistent")
            lines.append(f"  • 6,7M de patients sans médecin traitant en France, y compris dans les grandes villes (Ameli 2023)")
            lines.append(f"  → Ne jamais citer le chiffre national comme étant le chiffre local exact. Dire que le délai est probablement inférieur à {ville_propre} mais reste réel.")
            lines.append(f"  → Positionner {ville_propre} comme une opportunité : file active plus rapide qu'en rural, patientèle existante, différenciation par spécialisation")

    if densite_locale and densite_nationale:
        ratio = densite_locale / densite_nationale
        if ratio < 0.75:
            lines.append(f"Densité {spe_norm} locale : inférieure à la moyenne ({densite_locale}/100k hab vs {densite_nationale}/100k national) — forte demande non couverte")
        elif ratio > 1.25:
            lines.append(f"Densité {spe_norm} locale : supérieure à la moyenne ({densite_locale}/100k hab vs {densite_nationale}/100k national) — opportunité de spécialisation et file active rapide")
        else:
            lines.append(f"Densité {spe_norm} locale : comparable à la moyenne ({densite_locale}/100k hab vs {densite_nationale}/100k national)")
    elif densite_nationale and spe_norm:
        lines.append(f"Densité nationale {spe_norm} : {densite_nationale} médecins/100 000 hab (CNOM 2023)")

    # ── Benchmarks nationaux ──
    benchmarks = DEMOGRAPHICS.get("benchmarks_nationaux", {})

    # Revenus moyens par spécialité
    if spe_norm:
        revenus = benchmarks.get("revenus_moyens_bnc", {}).get("par_specialite", {})
        if spe_norm in revenus:
            revenu_moy = revenus[spe_norm]
            lines.append(f"Revenu moyen BNC {spe_norm} : {revenu_moy:,.0f}€/an (CARMF 2023) — avant charges sociales (~42%)")

    # Charge administrative benchmark
    charges = benchmarks.get("charges_admin", {})
    if charges:
        lines.append(f"Charge administrative moyenne nationale : {charges.get('heures_par_semaine_moyenne', 11.4)}h/sem (CNOM/URPS 2023)")

    # Téléconsultation
    tele = benchmarks.get("teleconsultation", {})
    if tele and spe_norm:
        taux_spe = tele.get("par_specialite", {}).get(spe_norm)
        taux_national = tele.get("taux_adoption_national", 0.08)
        if taux_spe:
            lines.append(f"Téléconsultation {spe_norm} : {round(taux_spe*100)}% des consultations vs {round(taux_national*100)}% national (CPAM 2023)")
        if spe_norm == "Médecine générale":
            lines.append(f"Potentiel téléconsultation MG non équipé : +{tele.get('revenus_additionnels_generaliste_mois', 850)}€/mois estimés")

    # ── Motifs de consultation ──
    motifs_data = DEMOGRAPHICS.get("motifs_consultation", {})
    spe_key_motifs = None
    if spe_norm == "Médecine générale": spe_key_motifs = "medecine_generale"
    elif "Gynécologie médicale" in (spe_norm or ""): spe_key_motifs = "gynecologie_medicale"
    elif spe_norm == "Psychiatrie": spe_key_motifs = "psychiatrie"
    elif spe_norm == "Pédiatrie": spe_key_motifs = "pediatrie"
    elif spe_norm == "Cardiologie": spe_key_motifs = "cardiologie"

    if spe_key_motifs and spe_key_motifs in motifs_data:
        motifs = motifs_data[spe_key_motifs]
        top = motifs.get("top_motifs", [])[:3]
        if top:
            lines.append(f"\nTop 3 motifs consultation {spe_norm} (CPAM/Ameli 2023) :")
            for m in top:
                lines.append(f"• {m['motif']} ({round(m['part']*100)}%)")
        # Données spécifiques
        if spe_key_motifs == "gynecologie_medicale":
            lines.append(f"Endométriose : délai diagnostic moyen {motifs.get('delai_diagnostic_endometriose_ans', 7)} ans — 10% des femmes en âge de procréer")
        elif spe_key_motifs == "psychiatrie":
            lines.append(f"Accès psychiatre libéral : délai moyen {motifs.get('delai_acces_psychiatre_liberal_semaines', 8.5)} semaines — 62% sans psychiatre référent")
        elif spe_key_motifs == "pediatrie":
            lines.append(f"TDAH : délai diagnostic {motifs.get('delai_diagnostic_tdah_mois', 18)} mois en moyenne — 5.5% des enfants concernés")

    # ── Niches sous-dotées ──
    if spe_norm and spe_norm in DEMOGRAPHICS.get("niches_sous_dotees", {}):
        niches = DEMOGRAPHICS["niches_sous_dotees"][spe_norm]
        if niches:
            lines.append(f"\nOPPORTUNITÉS DE DÉVELOPPEMENT — {spe_norm.upper()} (à mentionner si le score développement est faible) :")
            for niche in niches[:3]:
                lines.append(f"• {niche['niche']} : {niche['opportunite']}")

    # ── Aides zones sous-dotées ──
    if dept_info and dept_info.get("type") == "sous_dote":
        aides = DEMOGRAPHICS.get("aides_zones_sous_dotees", [])
        if aides:
            lines.append("\nAIDES DISPONIBLES ZONE SOUS-DOTÉE :")
            for aide in aides[:4]:
                lines.append(f"• {aide}")

    # ── Tendances contextuelles ──
    tendances = DEMOGRAPHICS.get("tendances_2024_2025", {})
    opportunites = tendances.get("opportunites_identifiees", [])
    if opportunites and spe_norm:
        # Filtrer les tendances pertinentes selon la spécialité
        relevant = [o for o in opportunites if
            any(kw in o.lower() for kw in [
                spe_norm.lower()[:6],
                'téléconsultation' if 'téléconsultation' in o.lower() else '',
                'coordination' if 'cardio' in (spe_norm or '').lower() else '',
            ]) and o]
        if relevant:
            lines.append(f"\nTENDANCES SECTORIELLES 2024 (DREES/CNOM) :")
            for t in relevant[:2]:
                lines.append(f"• {t}")

    return "\n".join(lines) if lines else ""

# ─────────────────────────────────────────
app = FastAPI(title="Bigdoc", docs_url=None, redoc_url=None)
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://bigdoc.fr"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")

anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ─────────────────────────────────────────
@app.on_event("startup")
async def startup():
    init_db()
    init_products()
    init_admin()
    load_demographics()
    # Vérifier que le modèle Anthropic est accessible
    try:
        test = anthropic_client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=10,
            messages=[{"role": "user", "content": "ok"}]
        )
        logger.info(f"✅ Modèle Anthropic OK : {ANTHROPIC_MODEL}")
    except anthropic.NotFoundError:
        logger.error(f"""
╔══════════════════════════════════════════════════════╗
║  ⚠️  MODÈLE ANTHROPIC INTROUVABLE                   ║
║                                                      ║
║  Le modèle '{ANTHROPIC_MODEL}' n'existe plus.       ║
║                                                      ║
║  → Ouvre Z:\\.env                                   ║
║  → Change ANTHROPIC_MODEL=claude-sonnet-4-5         ║
║    par le nouveau nom de modèle Anthropic            ║
║  → Redémarre le serveur                              ║
║                                                      ║
║  Modèles disponibles : console.anthropic.com/models  ║
╚══════════════════════════════════════════════════════╝
        """)
    except Exception as e:
        logger.warning(f"⚠️  Impossible de vérifier le modèle au démarrage : {e}")


# ─────────────────────────────────────────
# MODÈLES
# ─────────────────────────────────────────
class InstallationRequest(BaseModel):
    session_id: str
    email: str
    specialite: str
    zone: str
    type_exercice: str = ""
    horizon: str = ""
    inquietudes: str = ""

@app.post("/api/installation")
async def analyse_installation(req: InstallationRequest):
    """Analyse un projet d'installation médicale."""
    try:
        demo = DEMOGRAPHICS or {}
        aides = demo.get("aides_zones_sous_dotees", [])
        cpts_info = demo.get("donnees_cpts", {})
        drees = demo.get("donnees_drees_etat", {})

        prompt = f"""Tu es le Dr Bigdoc, consultant cabinet médical. Un médecin veut s'installer.

RÈGLE FONDAMENTALE — NE JAMAIS IGNORER :
Les zones "dites surdotées" manquent elles aussi de médecins. En France, les besoins médicaux sont non couverts PARTOUT.
→ 6,7M de patients sans médecin traitant (Ameli 2023), y compris à Paris et dans les grandes métropoles
→ Délai moyen RDV spécialiste : 49 jours même en Île-de-France — les patients attendent partout
→ Ne jamais décourager une installation. Toujours montrer les opportunités de différenciation.
→ Zone à forte densité = file active rapide, patientèle solvable, opportunité de spécialisation
→ Si la zone est "dite surdotée" : le préciser avec les guillemets et expliquer que les besoins existent quand même

PROJET :
- Spécialité : {req.specialite}
- Zone cible : {req.zone}
- Type d'exercice souhaité : {req.type_exercice or 'non précisé'}
- Horizon : {req.horizon or 'non précisé'}
- Inquiétudes : {req.inquietudes or 'non précisées'}

DONNÉES DISPONIBLES :
- Aides à l'installation : {json.dumps(aides[:5], ensure_ascii=False)}
- CPTS en France : {cpts_info.get('etat_deploiement', {}).get('nombre_cpts_france', 903)} actives — financement socle {cpts_info.get('financement', {}).get('dotation_socle_annuelle_euros', 150000)}€/an
- 5 800 communes en Zone d'Intervention Prioritaire ARS (déserts médicaux) — aides CAIM jusqu'à 50 000€

Réponds UNIQUEMENT en JSON valide :
{{
  "titre": "titre accrocheur du projet (10 mots max)",
  "message": "analyse bienveillante du projet en 3-4 phrases — chiffrer, sourcer, rassurer. Ton Dr Bigdoc.",
  "territoire": {{
    "stats": [
      {{"val": "chiffre clé", "label": "ce que ça signifie", "source": "source", "color": "#10B981 ou #EF4444 ou #6366F1"}},
      {{"val": "chiffre clé", "label": "ce que ça signifie", "source": "source", "color": "#couleur"}},
      {{"val": "chiffre clé", "label": "ce que ça signifie", "source": "source", "color": "#couleur"}}
    ]
  }},
  "aides": [
    {{"nom": "nom aide", "montant": "montant", "condition": "condition d'éligibilité"}},
    {{"nom": "nom aide", "montant": "montant", "condition": "condition"}}
  ],
  "checklist": [
    "étape 1",
    "étape 2",
    "étape 3",
    "étape 4",
    "étape 5"
  ]
}}"""

        message = anthropic_client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}]
        )

        raw = message.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        analyse = json.loads(raw)
        return {"analyse": analyse}

    except Exception as e:
        logger.error(f"❌ Erreur installation : {e}")
        raise HTTPException(status_code=500, detail=str(e))

class DiagnosticRequest(BaseModel):
    session_id: str
    reponses: dict
    texte_libre: str = ""
    turnstile_token: str = ""
    specialite: str = ""
    ville: str = ""


class LeadRequest(BaseModel):
    session_id: str
    email: EmailStr
    prenom: str = ""
    nom: str = ""
    specialite: str = ""
    ville: str = ""
    rgpd_consent: bool = True


class DeleteRequest(BaseModel):
    email: EmailStr


# ─────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────
async def verify_turnstile(token: str, ip: str) -> bool:
    """Vérifie le token Cloudflare Turnstile."""
    if not TURNSTILE_SECRET or not token:
        return True  # dev mode sans clé
    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data={"secret": TURNSTILE_SECRET, "response": token, "remoteip": ip}
        )
        return r.json().get("success", False)


def build_diagnostic_prompt(reponses: dict, texte_libre: str, specialite: str = "", ville: str = "", catalogue: str = "") -> str:
    """Construit le prompt utilisateur à partir des réponses."""
    lines = ["Voici les réponses du médecin au questionnaire de diagnostic :\n"]

    # Contexte médecin
    if specialite or ville:
        ctx = []
        if specialite: ctx.append(f"Spécialité : {specialite}")
        if ville:      ctx.append(f"Ville/zone : {ville}")
        lines.append("PROFIL DU MÉDECIN :")
        lines.extend([f"• {c}" for c in ctx])
        lines.append("")

    # Contexte démographique
    demo_context = get_demographic_context(specialite, ville)
    if demo_context:
        lines.append(demo_context)
        lines.append("")

    # Catalogue dynamique
    if catalogue:
        lines.append("CATALOGUE SERVICES BIGDOC ACTUEL (utiliser ces prix exacts) :")
        lines.append(catalogue)
        lines.append("")

    labels = {
        "phase":         "Phase du cabinet",
        "admin":         "Charge administrative",
        "materiel":      "État du matériel et stocks",
        "informatique":  "Infrastructure informatique",
        "teleconsult":   "Situation téléconsultation",
        "compta":        "Comptabilité et trésorerie",
        "charge":        "Charge mentale hors soins",
        "financement":   "Projets de financement",
        "developpement": "Projets de développement",
    }

    # Map option values to readable labels
    option_map = {}
    for q in QUESTIONNAIRE:
        option_map[q["id"]] = {opt["value"]: opt["label"] for opt in q["options"]}

    for qid, val in reponses.items():
        label = labels.get(qid, qid)
        # Gérer les multi_select (liste de valeurs)
        if isinstance(val, list):
            opts = option_map.get(qid, {})
            readable = ", ".join([opts.get(v, v) for v in val]) if val else "Aucune sélection"
        else:
            readable = option_map.get(qid, {}).get(str(val), str(val))
        lines.append(f"• {label} : {readable}")

    if texte_libre.strip():
        lines.append(f"\nDescription libre du médecin :\n\"{texte_libre.strip()}\"")

    lines.append("\nProduis le bilan complet en JSON strict selon tes instructions.")
    return "\n".join(lines)


# ─────────────────────────────────────────
# ROUTES PRINCIPALES
# ─────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def index():
    with open("static/index.html", "r", encoding="utf-8") as f:
        return f.read()


@app.get("/bilan/{session_id}", response_class=HTMLResponse)
async def bilan_page(session_id: str):
    with open("static/bilan.html", "r", encoding="utf-8") as f:
        return f.read()


@app.get("/partage/{token}", response_class=HTMLResponse)
async def partage_page(token: str):
    with open("static/bilan.html", "r", encoding="utf-8") as f:
        return f.read()


@app.get("/api/questionnaire")
async def get_questionnaire():
    return {"questions": QUESTIONNAIRE}


@app.post("/api/diagnostic")
@limiter.limit("5/hour")
async def run_diagnostic(request: Request, body: DiagnosticRequest):
    """Lance le diagnostic IA — cœur du système."""

    # Anti-spam Turnstile
    client_ip = request.client.host
    if not await verify_turnstile(body.turnstile_token, client_ip):
        raise HTTPException(status_code=429, detail="Vérification anti-bot échouée")

    # Construire le prompt avec catalogue dynamique
    catalogue = get_catalogue_for_prompt()
    user_prompt = build_diagnostic_prompt(body.reponses, body.texte_libre, body.specialite, body.ville, catalogue)

    # Appel Claude Sonnet
    try:
        message = anthropic_client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=6000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}]
        )
        raw = message.content[0].text.strip()

        # Nettoyer si Claude a quand même mis des backticks
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        if raw.endswith("```"):
            raw = raw[:-3]

        bilan = json.loads(raw.strip())

    except json.JSONDecodeError as e:
        logger.error(f"❌ JSON invalide reçu de Claude : {str(e)}")
        logger.error(f"   Contenu brut : {raw[:500] if 'raw' in dir() else 'non disponible'}")
        raise HTTPException(status_code=500, detail=f"Erreur parsing bilan : {str(e)}")
    except anthropic.AuthenticationError:
        logger.error("❌ Clé API Anthropic invalide")
        raise HTTPException(status_code=500, detail="Clé API invalide — vérifiez ANTHROPIC_API_KEY dans .env")
    except anthropic.NotFoundError:
        logger.error(f"❌ Modèle Anthropic introuvable : {ANTHROPIC_MODEL}")
        raise HTTPException(status_code=500, detail=f"Modèle {ANTHROPIC_MODEL} introuvable — mettez à jour ANTHROPIC_MODEL dans .env")
    except anthropic.RateLimitError:
        logger.error("❌ Rate limit Anthropic atteint")
        raise HTTPException(status_code=429, detail="Trop de requêtes — réessayez dans quelques secondes")
    except Exception as e:
        logger.error(f"❌ Erreur diagnostic inattendue : {type(e).__name__} : {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erreur : {str(e)}")

    # Sauvegarder
    diagnostic_id = save_diagnostic(body.session_id, bilan, body.reponses, body.texte_libre)

    # Générer token de partage
    share_token = secrets.token_urlsafe(16)
    if diagnostic_id:
        create_partage_token(diagnostic_id, share_token)

    # Envoyer email bilan (async, ne bloque pas la réponse)
    lead_info = get_lead_by_session(body.session_id)
    if lead_info and lead_info.get("email"):
        import asyncio
        asyncio.create_task(send_bilan_email(
            lead_info["email"],
            lead_info.get("prenom", ""),
            bilan
        ))

    return {
        "success": True,
        "bilan": bilan,
        "session_id": body.session_id,
        "share_token": share_token,
    }


@app.post("/api/lead")
@limiter.limit("10/hour")
async def capture_lead(request: Request, body: LeadRequest):
    """Capture le contact après affichage du score partiel."""
    lead_id = save_lead(
        body.session_id, body.email, body.prenom, body.specialite, body.ville, body.nom
    )
    return {"success": True, "lead_id": lead_id}


@app.get("/api/rapport/{session_id}")
async def generate_rapport(session_id: str):
    """Génère un rapport PDF personnalisé pour un diagnostic."""
    from rapport import generate_rapport_html

    bilan_data = get_diagnostic(session_id)
    if not bilan_data:
        raise HTTPException(status_code=404, detail="Diagnostic non trouvé")

    lead_info = get_lead_by_session(session_id)
    specialite = lead_info.get("specialite", "") if lead_info else ""
    ville = lead_info.get("ville", "") if lead_info else ""

    html = generate_rapport_html(bilan_data, lead_info, specialite, ville)

    try:
        import weasyprint
        pdf_bytes = weasyprint.HTML(string=html, base_url="/").write_pdf()
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="rapport-bigdoc-{session_id[:8]}.pdf"'}
        )
    except Exception as e:
        logger.error(f"❌ Erreur génération PDF : {e}")
        raise HTTPException(status_code=500, detail=f"Erreur génération PDF : {str(e)}")
    bilan = get_diagnostic(session_id)
    if not bilan:
        raise HTTPException(status_code=404, detail="Diagnostic non trouvé")
    return {"bilan": bilan}


@app.get("/api/partage/{token}")
async def get_partage(token: str):
    bilan = get_diagnostic_by_token(token)
    if not bilan:
        raise HTTPException(status_code=404, detail="Lien de partage invalide ou expiré")
    return {"bilan": bilan}


@app.get("/api/stats")
async def get_public_stats():
    """Stats anonymisées — pour la page d'accueil et le SEO."""
    return get_stats()


@app.get("/api/health")
async def health_check():
    """Healthcheck — vérifie que le modèle Anthropic répond."""
    status = {"bigdoc": "ok", "model": ANTHROPIC_MODEL, "model_status": "unknown"}
    try:
        anthropic_client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=5,
            messages=[{"role": "user", "content": "ping"}]
        )
        status["model_status"] = "ok"
    except anthropic.NotFoundError:
        status["model_status"] = "MODEL_DEPRECATED"
        status["bigdoc"] = "degraded"
        # Envoyer email d'alerte
        await send_alert_email(
            subject="⚠️ Bigdoc — Modèle Anthropic déprécié",
            body=f"""Le modèle '{ANTHROPIC_MODEL}' n'est plus disponible.

ACTION REQUISE :
1. Ouvre Z:\\.env
2. Change ANTHROPIC_MODEL={ANTHROPIC_MODEL}
   par le nouveau nom disponible sur console.anthropic.com/models
3. Redémarre le serveur

Le diagnostic Bigdoc ne fonctionne plus jusqu'à correction.
"""
        )
    except anthropic.AuthenticationError:
        status["model_status"] = "AUTH_ERROR"
        status["bigdoc"] = "degraded"
    except Exception as e:
        status["model_status"] = f"ERROR: {str(e)}"
        status["bigdoc"] = "degraded"

    return status


async def send_alert_email(subject: str, body: str):
    """Envoie un email d'alerte via Resend."""
    if not RESEND_API_KEY:
        logger.warning("RESEND_API_KEY non configuré — email d'alerte non envoyé")
        return
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
                json={
                    "from": FROM_EMAIL,
                    "to": [ALERT_EMAIL],
                    "subject": subject,
                    "text": body
                }
            )
        logger.info(f"✅ Email d'alerte envoyé à {ALERT_EMAIL}")
    except Exception as e:
        logger.error(f"❌ Erreur envoi email alerte : {e}")


class ChatReactionRequest(BaseModel):
    texte: str
    turnstile_token: str = ""


@app.post("/api/chat-reaction")
@limiter.limit("10/hour")
async def chat_reaction(request: Request, body: ChatReactionRequest):
    """Réaction immédiate au texte libre — avant la capture email."""
    client_ip = request.client.host
    if not await verify_turnstile(body.turnstile_token, client_ip):
        raise HTTPException(status_code=429, detail="Vérification anti-bot échouée")

    if len(body.texte.strip()) < 20:
        raise HTTPException(status_code=400, detail="Texte trop court")

    try:
        message = anthropic_client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=200,
            system=SYSTEM_PROMPT_CHAT_REACTION,
            messages=[{"role": "user", "content": body.texte}]
        )
        reaction = message.content[0].text.strip()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"reaction": reaction}


@app.post("/api/delete-my-data")
async def delete_data(body: DeleteRequest):
    """RGPD — droit à l'effacement."""
    deleted = delete_lead_data(body.email)
    if not deleted:
        raise HTTPException(status_code=404, detail="Email non trouvé dans notre base")
    return {"success": True, "message": "Vos données ont été supprimées."}


# ─────────────────────────────────────────
# ADMIN
# ─────────────────────────────────────────
def get_admin_from_request(request: Request) -> dict | None:
    """Extrait et vérifie le token de session depuis le cookie ou header."""
    token = request.cookies.get("admin_session") or request.headers.get("X-Admin-Token", "")
    return verify_admin_session(token)

def require_admin(request: Request) -> dict:
    """Dependency — lève 401 si pas authentifié."""
    admin = get_admin_from_request(request)
    if not admin:
        raise HTTPException(status_code=401, detail="Session expirée — veuillez vous reconnecter")
    return admin


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    if not is_admin_setup_done():
        return RedirectResponse("/admin/setup")
    with open("static/admin.html", "r", encoding="utf-8") as f:
        return f.read()

@app.get("/admin/setup", response_class=HTMLResponse)
async def admin_setup_page():
    if is_admin_setup_done():
        return RedirectResponse("/admin/login")
    with open("static/admin_setup.html", "r", encoding="utf-8") as f:
        return f.read()


class AdminSetupRequest(BaseModel):
    email: str
    username: str
    password: str


@app.post("/api/admin/setup")
async def admin_setup(body: AdminSetupRequest):
    if is_admin_setup_done():
        raise HTTPException(status_code=403, detail="Setup déjà effectué")
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Mot de passe trop court (8 caractères minimum)")
    if not "@" in body.email:
        raise HTTPException(status_code=400, detail="Email invalide")
    ok = setup_admin(body.email, body.username, body.password)
    if not ok:
        raise HTTPException(status_code=403, detail="Setup déjà effectué")
    return {"success": True}


@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page():
    with open("static/admin_login.html", "r", encoding="utf-8") as f:
        return f.read()

@app.get("/admin/reset-password", response_class=HTMLResponse)
async def admin_reset_page():
    with open("static/admin_reset.html", "r", encoding="utf-8") as f:
        return f.read()


class AdminLoginRequest(BaseModel):
    username: str
    password: str

class AdminResetRequestBody(BaseModel):
    email: str

class AdminNewPasswordBody(BaseModel):
    token: str
    password: str


@app.post("/api/admin/login")
async def admin_login(body: AdminLoginRequest, response: Response):
    try:
        admin = verify_admin(body.username, body.password)
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    if not admin:
        raise HTTPException(status_code=401, detail="Identifiants incorrects")
    token = create_admin_session(admin["id"] if admin.get("id") else 0)
    response.set_cookie(
        key="admin_session", value=token,
        httponly=True, secure=True, samesite="lax",
        max_age=86400
    )
    return {"success": True}


@app.post("/api/admin/logout")
async def admin_logout(request: Request, response: Response):
    token = request.cookies.get("admin_session", "")
    if token:
        delete_admin_session(token)
    response.delete_cookie("admin_session")
    return {"success": True}


@app.post("/api/admin/reset-request")
async def admin_reset_request(body: AdminResetRequestBody):
    """Envoie un email de reset/déblocage."""
    token = create_reset_token(body.email)
    if token:
        host = "dev.bigdoc.fr" if "dev" in body.email else "bigdoc.fr"
        reset_url = f"https://{host}/admin/reset-password?token={token}"
        try:
            import resend
            resend.api_key = RESEND_API_KEY
            resend.Emails.send({
                "from": FROM_EMAIL,
                "to": body.email,
                "subject": "Bigdoc Admin — Réinitialisation de votre mot de passe",
                "html": f"""
                <p>Bonjour,</p>
                <p>Cliquez sur le lien ci-dessous pour réinitialiser votre mot de passe admin Bigdoc :</p>
                <p><a href="{reset_url}">{reset_url}</a></p>
                <p>Ce lien expire dans 1 heure.</p>
                <p>Si vous n'avez pas demandé cette réinitialisation, ignorez cet email.</p>
                """
            })
        except Exception as e:
            logger.error(f"Erreur email reset: {e}")
    # Toujours retourner succès (sécurité — ne pas révéler si email existe)
    return {"success": True, "message": "Si cet email existe, un lien de réinitialisation a été envoyé."}


@app.post("/api/admin/reset-password")
async def admin_reset_password_route(body: AdminNewPasswordBody):
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Mot de passe trop court (8 caractères minimum)")
    ok = reset_admin_password(body.token, body.password)
    if not ok:
        raise HTTPException(status_code=400, detail="Lien invalide ou expiré")
    return {"success": True}


@app.get("/api/admin/me")
async def admin_me(request: Request):
    """Vérifie la session en cours."""
    admin = get_admin_from_request(request)
    if not admin:
        raise HTTPException(status_code=401, detail="Non authentifié")
    return {"username": admin.get("username"), "email": admin.get("email")}


@app.get("/api/admin/leads")
async def admin_leads(request: Request):
    require_admin(request)
    return get_all_leads()


# ── CRM — FICHE LEAD ──
@app.get("/api/admin/leads/{lead_id}")
async def get_lead_detail(lead_id: int, request: Request):
    require_admin(request)
    fiche = get_lead_fiche(lead_id)
    if not fiche:
        raise HTTPException(status_code=404, detail="Lead introuvable")
    return fiche

@app.patch("/api/admin/leads/{lead_id}/status")
async def update_status(lead_id: int, request: Request):
    require_admin(request)
    data = await request.json()
    status = data.get("status", "")
    note = data.get("note", "")
    if not update_lead_status(lead_id, status, note):
        raise HTTPException(status_code=400, detail=f"Statut invalide. Valeurs: {LEAD_STATUTS}")
    return {"success": True}

@app.post("/api/admin/leads/{lead_id}/notes")
async def add_note(lead_id: int, request: Request):
    require_admin(request)
    data = await request.json()
    content = data.get("content", "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="Note vide")
    event_id = add_lead_note(lead_id, content)
    return {"success": True, "event_id": event_id}

@app.delete("/api/admin/leads/{lead_id}/notes/{event_id}")
async def delete_note(lead_id: int, event_id: int, request: Request):
    require_admin(request)
    delete_lead_event(event_id)
    return {"success": True}

@app.patch("/api/admin/leads/{lead_id}/info")
async def update_info(lead_id: int, request: Request):
    require_admin(request)
    data = await request.json()
    update_lead_info(lead_id, data)
    return {"success": True}




@app.get("/api/admin/settings")
async def get_settings_route(request: Request):
    require_admin(request)
    return get_app_settings()


@app.post("/api/admin/settings")
async def save_settings_route(request: Request):
    require_admin(request)
    data = await request.json()
    save_app_settings(data)
    return {"success": True}


@app.get("/api/products")
async def list_products():
    """Catalogue public — pour le bilan."""
    return get_products(actif_only=True)


@app.get("/api/public-settings")
async def public_settings():
    """Paramètres publics non-sensibles pour le front."""
    s = get_app_settings()
    return {
        "calendly_url": s.get("calendly_url", ""),
        "email_contact": s.get("email_contact", "bonjour@bigdoc.fr"),
        "telephone":     s.get("telephone", ""),
        "seuil_rdv":     s.get("seuil_rdv", 50),
    }


@app.get("/api/admin/products")
async def admin_list_products(request: Request):
    require_admin(request)
    return get_products()


@app.post("/api/admin/products")
async def admin_create_product(request: Request):
    require_admin(request)
    data = await request.json()
    product_id = create_product(data)
    return {"success": True, "id": product_id}


@app.put("/api/admin/products/{product_id}")
async def admin_update_product(product_id: int, request: Request):
    require_admin(request)
    data = await request.json()
    update_product(product_id, data)
    return {"success": True}


@app.delete("/api/admin/products/{product_id}")
async def admin_delete_product(product_id: int, request: Request):
    require_admin(request)
    delete_product(product_id)
    return {"success": True}


@app.post("/api/admin/products/{product_id}/toggle")
async def admin_toggle_product(product_id: int, request: Request):
    require_admin(request)
    actif = toggle_product(product_id)
    return {"success": True, "actif": actif}


@app.get("/api/admin/stripe-stats")
async def stripe_stats_route(request: Request):
    """Stats CA depuis Stripe."""
    require_admin(request)

    settings = get_app_settings()
    sk = settings.get("stripe_sk") or STRIPE_SECRET_KEY
    mode = settings.get("stripe_mode", "test")

    if not sk:
        return {"ca_mois": 0, "ca_total": 0, "abonnes": 0, "transactions_mois": 0, "mode": mode}

    try:
        import stripe as stripe_lib
        stripe_lib.api_key = sk
        from datetime import datetime
        debut_mois = int(datetime(datetime.now().year, datetime.now().month, 1).timestamp())

        charges = stripe_lib.BalanceTransaction.list(limit=100)
        ca_total = sum(t.amount for t in charges.data if t.type == "charge")

        charges_mois = stripe_lib.BalanceTransaction.list(limit=100, created={"gte": debut_mois})
        ca_mois = sum(t.amount for t in charges_mois.data if t.type == "charge")

        subs = stripe_lib.Subscription.list(status="active", limit=100)
        abonnes = len(subs.data)

        return {"ca_mois": ca_mois, "ca_total": ca_total, "abonnes": abonnes,
                "transactions_mois": len(charges_mois.data), "mode": mode}
    except Exception as e:
        return {"ca_mois": 0, "ca_total": 0, "abonnes": 0, "transactions_mois": 0, "mode": mode}


# ─────────────────────────────────────────
# EMAIL POST-DIAGNOSTIC
# ─────────────────────────────────────────
async def send_bilan_email(email: str, prenom: str, bilan: dict):
    """Envoie le bilan par email après le diagnostic."""
    if not RESEND_API_KEY or not email:
        return

    score = bilan.get("score_global", 0)
    niveau = bilan.get("niveau", "")
    heures = bilan.get("heures_perdues_semaine", 0)
    euros = bilan.get("euros_evitables_an", 0)
    reco = bilan.get("recommandation_principale", {})
    quick_wins = bilan.get("quick_wins", [])
    message_bilan = bilan.get("message_bilan", "")
    alerte = bilan.get("alerte_urgente", "")

    prenom_display = prenom or "Docteur"
    quick_wins_text = "\n".join([f"• {w}" for w in quick_wins])
    alerte_text = f"\n⚠️ ALERTE URGENTE : {alerte}\n" if alerte else ""

    body = f"""Bonjour {prenom_display},

Votre diagnostic Bigdoc est prêt.

═══════════════════════════════
SCORE DE CONFORT : {score}/100
{niveau}
═══════════════════════════════

{message_bilan}
{alerte_text}
IMPACT CHIFFRÉ
• Heures perdues par semaine : {heures}h
• Euros récupérables par an : {euros:,.0f} €

ORDONNANCE BIGDOC
{reco.get('service', '')} — {reco.get('tarif', '')}
{reco.get('justification', '')}

3 ACTIONS CETTE SEMAINE
{quick_wins_text}

─────────────────────────────────
Pour aller plus loin, contactez Bigdoc :
https://realmedservices.com/contact

Bigdoc · un service RMS
Vous soignez les gens, on soigne vos problèmes.
"""

    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
                json={
                    "from": FROM_EMAIL,
                    "to": [email],
                    "subject": f"Votre diagnostic Bigdoc — {score}/100 · {niveau}",
                    "text": body
                }
            )
        logger.info(f"✅ Email bilan envoyé à {email}")
    except Exception as e:
        logger.error(f"❌ Erreur email bilan : {e}")


# ─────────────────────────────────────────
# STRIPE — Checkout depuis le catalogue
# ─────────────────────────────────────────
class CheckoutRequest(BaseModel):
    session_id: str
    produit: str   # product ID (int as string) depuis le catalogue
    email: str = ""


@app.post("/api/create-checkout")
async def create_checkout(body: CheckoutRequest):
    """Crée une session Stripe Checkout depuis le catalogue."""
    import stripe
    settings = get_app_settings()
    sk = settings.get("stripe_sk") or STRIPE_SECRET_KEY
    if not sk:
        raise HTTPException(status_code=400, detail="Stripe non configuré — ajoutez vos clés dans les paramètres admin")
    stripe.api_key = sk

    # Chercher le produit dans le catalogue — par ID ou par clé
    product = None
    try:
        product = get_product(int(body.produit))
    except (ValueError, TypeError):
        pass

    if not product:
        key_map = {
            "serenite": 9,
            "confort": 10,
            "cabinet_libere": 11,
            "plan_action": 2,
        }
        pid = key_map.get(body.produit.lower())
        if pid:
            product = get_product(pid)

    if not product:
        raise HTTPException(status_code=400, detail="Produit introuvable dans le catalogue")

    nom = product["nom"]
    prix_cents = product["prix_cents"]
    is_sub = product["type"] == "abonnement"
    stripe_price_id = product.get("stripe_price_id", "") or ""

    if prix_cents == 0:
        raise HTTPException(status_code=400, detail="Ce service est gratuit")

    try:
        mode = "subscription" if is_sub else "payment"

        if stripe_price_id:
            line_item = {"price": stripe_price_id, "quantity": 1}
        else:
            price_data = {
                "currency": "eur",
                "product_data": {"name": nom},
                "unit_amount": prix_cents,
            }
            if is_sub:
                price_data["recurring"] = {"interval": "month"}
            line_item = {"price_data": price_data, "quantity": 1}

        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            mode=mode,
            line_items=[line_item],
            customer_email=body.email or None,
            success_url=f"https://bigdoc.fr/?session={body.session_id}&paiement=ok",
            cancel_url=f"https://bigdoc.fr/?session={body.session_id}&paiement=cancel",
            metadata={"session_id": body.session_id, "produit_id": str(product["id"]), "produit_nom": nom}
        )
        return {"checkout_url": session.url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/stripe-webhook")
async def stripe_webhook(request: Request):
    """Webhook Stripe — gère paiements, quotes, échecs."""
    import stripe
    settings = get_app_settings()
    sk = settings.get("stripe_sk") or STRIPE_SECRET_KEY
    stripe.api_key = sk

    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    webhook_secret = settings.get("stripe_webhook_secret") or STRIPE_WEBHOOK_SECRET

    if not webhook_secret:
        # Pas de secret configuré — on accepte mais on log un warning
        logger.warning("⚠️ Webhook reçu sans secret configuré — vérification signature désactivée")
        try:
            event = stripe.Event.construct_from(
                json.loads(payload), stripe.api_key
            )
        except Exception:
            raise HTTPException(status_code=400, detail="Payload invalide")
    else:
        try:
            event = stripe.Webhook.construct_event(payload, sig, webhook_secret)
        except Exception:
            raise HTTPException(status_code=400, detail="Signature Stripe invalide")

    evt_type = event["type"]
    obj = event["data"]["object"]
    logger.info(f"📩 Webhook Stripe : {evt_type}")

    # ── Paiement checkout réussi ──
    if evt_type == "checkout.session.completed":
        meta = obj.get("metadata", {})
        session_id = meta.get("session_id", "")
        produit_nom = meta.get("produit_nom", "")
        customer_email = obj.get("customer_email") or obj.get("customer_details", {}).get("email", "")
        logger.info(f"✅ Checkout — session {session_id} / {produit_nom} / {customer_email}")

    # ── Facture payée ──
    elif evt_type == "invoice.payment_succeeded":
        customer_email = obj.get("customer_email", "")
        amount = obj.get("amount_paid", 0)
        invoice_id = obj.get("id", "")
        logger.info(f"✅ Invoice payée — {invoice_id} / {amount/100:.2f}€ / {customer_email}")
        # Envoyer email de confirmation si Resend configuré
        if customer_email and RESEND_API_KEY:
            try:
                import resend
                resend.api_key = RESEND_API_KEY
                resend.Emails.send({
                    "from": FROM_EMAIL,
                    "to": customer_email,
                    "subject": "✅ Paiement confirmé — RMS",
                    "html": f"""
                    <p>Bonjour,</p>
                    <p>Votre paiement de <strong>{amount/100:.2f} €</strong> a bien été reçu.</p>
                    <p>Numéro de facture : <strong>{invoice_id}</strong></p>
                    <p>L'équipe RMS vous contactera très prochainement pour démarrer l'accompagnement.</p>
                    <p>Cordialement,<br>L'équipe RMS</p>
                    """
                })
            except Exception as e:
                logger.error(f"Erreur email confirmation : {e}")

    # ── Paiement échoué ──
    elif evt_type == "invoice.payment_failed":
        customer_email = obj.get("customer_email", "")
        invoice_id = obj.get("id", "")
        logger.warning(f"❌ Paiement échoué — {invoice_id} / {customer_email}")
        if customer_email and RESEND_API_KEY:
            try:
                import resend
                resend.api_key = RESEND_API_KEY
                resend.Emails.send({
                    "from": FROM_EMAIL,
                    "to": customer_email,
                    "subject": "⚠️ Problème de paiement — RMS",
                    "html": f"""
                    <p>Bonjour,</p>
                    <p>Votre paiement n'a pas pu être traité.</p>
                    <p>Veuillez vérifier vos coordonnées bancaires ou nous contacter à <a href="mailto:{FROM_EMAIL}">{FROM_EMAIL}</a>.</p>
                    <p>Cordialement,<br>L'équipe RMS</p>
                    """
                })
            except Exception as e:
                logger.error(f"Erreur email échec : {e}")

    # ── Devis accepté ──
    elif evt_type == "quote.accepted":
        customer_id = obj.get("customer", "")
        quote_number = obj.get("number", "")
        logger.info(f"✅ Devis accepté — {quote_number} / customer {customer_id}")

    return {"received": True}




# ─────────────────────────────────────────
# DEVIS — STRIPE QUOTES
# ─────────────────────────────────────────
class QuoteRequest(BaseModel):
    email: str
    prenom: str = ""
    nom: str = ""
    specialite: str = ""
    ville: str = ""
    produit_nom: str = ""
    prix_ht: float = 0
    tva: float = 20.0
    note: str = ""
    expires_jours: int = 30
    session_id: str = ""


def _get_stripe():
    import stripe as stripe_lib
    settings = get_app_settings()
    sk = settings.get("stripe_sk") or STRIPE_SECRET_KEY
    if not sk:
        raise HTTPException(status_code=400, detail="Stripe non configuré")
    stripe_lib.api_key = sk
    return stripe_lib


@app.get("/api/admin/quotes")
async def list_quotes(request: Request):
    require_admin(request)
    stripe = _get_stripe()
    try:
        quotes = stripe.Quote.list(limit=50)
        result = []
        for q in quotes.data:
            customer = None
            if q.customer:
                try:
                    customer = stripe.Customer.retrieve(q.customer)
                except Exception:
                    pass
            result.append({
                "id": q.id,
                "status": q.status,
                "amount_total": q.amount_total,
                "currency": q.currency,
                "created": q.created,
                "expires_at": q.expires_at,
                "customer_email": customer.email if customer else q.get("customer_email", ""),
                "customer_name": customer.name if customer else "",
                "description": q.description or "",
                "pdf": q.pdf if hasattr(q, 'pdf') else None,
            })
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/admin/quotes")
async def create_quote(request: Request, body: QuoteRequest):
    require_admin(request)
    stripe = _get_stripe()
    try:
        from datetime import datetime, timedelta

        # Créer ou récupérer le customer Stripe
        customers = stripe.Customer.list(email=body.email, limit=1)
        if customers.data:
            customer = customers.data[0]
        else:
            customer = stripe.Customer.create(
                email=body.email,
                name=f"Dr. {body.prenom} {body.nom}".strip(),
                metadata={
                    "specialite": body.specialite,
                    "ville": body.ville,
                    "session_id": body.session_id,
                }
            )

        prix_ht_cents = int(round(body.prix_ht * 100))
        expires_at = int((datetime.now() + timedelta(days=body.expires_jours)).timestamp())

        # price_data dans les Quotes exige un product ID existant
        # On crée un produit Stripe inline puis on l'utilise
        product = stripe.Product.create(
            name=body.produit_nom or "Prestation RMS",
            description=f"Dr. {body.prenom} {body.nom} — {body.specialite} — {body.ville}".strip(" —"),
        )

        quote = stripe.Quote.create(
            customer=customer.id,
            expires_at=expires_at,
            description=body.note or f"Accompagnement cabinet médical — {body.produit_nom}",
            collection_method="send_invoice",
            invoice_settings={"days_until_due": 30},
            line_items=[{
                "price_data": {
                    "currency": "eur",
                    "product": product.id,
                    "unit_amount": prix_ht_cents,
                    "tax_behavior": "exclusive",
                },
                "quantity": 1,
            }],
        )

        return {"success": True, "quote_id": quote.id, "status": quote.status}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/admin/quotes/{quote_id}/finalize")
async def finalize_quote(quote_id: str, request: Request):
    """Finalise le devis et envoie le PDF par email via Resend."""
    require_admin(request)
    stripe = _get_stripe()
    try:
        # Finaliser le quote (lui assigne un numéro)
        quote = stripe.Quote.finalize_quote(quote_id)

        # Récupérer l'email du customer
        customer_email = ""
        if quote.customer:
            try:
                customer = stripe.Customer.retrieve(quote.customer)
                customer_email = customer.email or ""
            except Exception:
                pass

        # Télécharger le PDF
        pdf_response = stripe.Quote.pdf(quote_id)
        pdf_bytes = pdf_response.read()

        # Envoyer par email via Resend
        if customer_email and RESEND_API_KEY:
            import resend, base64
            resend.api_key = RESEND_API_KEY
            resend.Emails.send({
                "from": FROM_EMAIL,
                "to": customer_email,
                "subject": f"Votre devis RMS — {quote.number}",
                "html": f"""
                <p>Bonjour,</p>
                <p>Veuillez trouver ci-joint votre devis <strong>{quote.number}</strong>.</p>
                <p>Ce devis est valable jusqu'au {quote.expires_at}.</p>
                <p>Pour toute question, contactez-nous à <a href="mailto:{FROM_EMAIL}">{FROM_EMAIL}</a>.</p>
                <p>Cordialement,<br>L'équipe RMS</p>
                """,
                "attachments": [{
                    "filename": f"devis-{quote.number}.pdf",
                    "content": base64.b64encode(pdf_bytes).decode(),
                }]
            })

        return {"success": True, "status": quote.status, "number": quote.number, "email_sent": bool(customer_email)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/admin/quotes/{quote_id}/accept")
async def accept_quote(quote_id: str, request: Request):
    """Accepte le devis et crée l'invoice."""
    require_admin(request)
    stripe = _get_stripe()
    try:
        quote = stripe.Quote.accept(quote_id)
        return {"success": True, "status": quote.status, "invoice": getattr(quote, 'invoice', None)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class InvoiceRequest(BaseModel):
    email: str
    prenom: str = ""
    nom: str = ""
    description: str = ""
    prix_ht: float = 0
    tva: float = 20.0


@app.post("/api/admin/invoices")
async def create_direct_invoice(request: Request, body: InvoiceRequest):
    """Crée et envoie une facture Stripe directement sans devis."""
    require_admin(request)
    stripe = _get_stripe()
    try:
        # Créer ou récupérer le customer
        customers = stripe.Customer.list(email=body.email, limit=1)
        if customers.data:
            customer = customers.data[0]
        else:
            customer = stripe.Customer.create(
                email=body.email,
                name=f"Dr. {body.prenom} {body.nom}".strip(),
            )

        prix_ht_cents = int(round(body.prix_ht * 100))

        # Créer l'invoice item
        stripe.InvoiceItem.create(
            customer=customer.id,
            amount=prix_ht_cents,
            currency="eur",
            description=body.description,
        )

        # Créer et finaliser l'invoice
        invoice = stripe.Invoice.create(
            customer=customer.id,
            collection_method="send_invoice",
            days_until_due=30,
            auto_advance=True,
        )
        invoice = stripe.Invoice.finalize_invoice(invoice.id)
        stripe.Invoice.send_invoice(invoice.id)

        return {"success": True, "invoice_id": invoice.id, "status": invoice.status}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/admin/quotes/{quote_id}/cancel")
async def cancel_quote(quote_id: str, request: Request):
    require_admin(request)
    stripe = _get_stripe()
    try:
        quote = stripe.Quote.cancel(quote_id)
        return {"success": True, "status": quote.status}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



# ─────────────────────────────────────────
# EVAL — Routes admin
# ─────────────────────────────────────────
import threading, uuid as _uuid

_eval_jobs = {}  # job_id → {done, total, status}

EVAL_CASES_FILE   = Path("eval_cases.json")
EVAL_RESULTS_FILE = Path("eval_results.json")
EVAL_BATCHES_FILE = Path("eval_batches.json")


def _run_eval_thread(job_id: str, cases: list):
    """Lance les cas en arrière-plan."""
    _eval_jobs[job_id] = {"done": 0, "total": len(cases), "status": "running"}
    results = {}
    if EVAL_RESULTS_FILE.exists():
        for r in json.loads(EVAL_RESULTS_FILE.read_text()):
            results[r["id"]] = r

    for case in cases:
        try:
            # Utiliser les fonctions directement (pas de circular import)
            context = get_demographic_context(case["specialite"], case["ville"])
            user_prompt = build_diagnostic_prompt(
                reponses=case["reponses"],
                texte_libre=case.get("texte_libre", ""),
                specialite=case["specialite"],
                ville=case["ville"],
                catalogue=""
            )
            if context:
                user_prompt = context + "\n\n" + user_prompt

            response = anthropic_client.messages.create(
                model=ANTHROPIC_MODEL,
                max_tokens=4000,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}]
            )
            raw = response.content[0].text.strip()
            try:
                bilan = json.loads(raw)
            except Exception:
                import re as _re
                match = _re.search(r'\{.*\}', raw, _re.DOTALL)
                bilan = json.loads(match.group()) if match else {"erreur": "JSON invalide"}

            results[case["id"]] = {
                "id": case["id"], "label": case["label"],
                "specialite": case["specialite"], "ville": case["ville"],
                "texte_libre": case.get("texte_libre", ""),
                "bilan": bilan, "status": "ok" if "score_global" in bilan else "erreur",
                "timestamp": datetime.now().isoformat()
            }
        except Exception as e:
            results[case["id"]] = {
                "id": case["id"], "label": case.get("label", ""),
                "specialite": case.get("specialite", ""), "ville": case.get("ville", ""),
                "texte_libre": case.get("texte_libre", ""),
                "bilan": {}, "status": "erreur", "erreur": str(e),
                "timestamp": datetime.now().isoformat()
            }
        _eval_jobs[job_id]["done"] += 1
        EVAL_RESULTS_FILE.write_text(json.dumps(list(results.values()), ensure_ascii=False, indent=2))
        time.sleep(1.2)

    _eval_jobs[job_id]["status"] = "done"


EVAL_FEEDBACKS_FILE = Path("eval_feedbacks.json")


@app.get("/api/admin/eval/export-word")
async def eval_export_word(request: Request):
    """Génère et télécharge le document Word de relecture."""
    require_admin(request)
    if not EVAL_RESULTS_FILE.exists():
        raise HTTPException(status_code=404, detail="Aucun résultat d'évaluation")

    import subprocess, tempfile
    output = Path(tempfile.mktemp(suffix=".docx"))
    script = Path("/app/generate_eval_word.py")
    if not script.exists():
        raise HTTPException(status_code=404, detail="Script de génération introuvable")

    result = subprocess.run(
        ["python3", str(script), str(EVAL_RESULTS_FILE), str(output)],
        capture_output=True, text=True
    )
    if result.returncode != 0 or not output.exists():
        raise HTTPException(status_code=500, detail=f"Erreur génération : {result.stderr[:200]}")

    from fastapi.responses import FileResponse
    return FileResponse(
        path=str(output),
        filename=f"bigdoc-eval-{datetime.now().strftime('%Y%m%d')}.docx",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )


@app.post("/api/admin/eval/feedback")
async def save_eval_feedback(request: Request):
    require_admin(request)
    data = await request.json()
    EVAL_FEEDBACKS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    return {"success": True, "count": len(data)}


@app.get("/api/admin/eval/feedback")
async def get_eval_feedback(request: Request):
    require_admin(request)
    if not EVAL_FEEDBACKS_FILE.exists():
        return {}
    return json.loads(EVAL_FEEDBACKS_FILE.read_text())



async def eval_run(request: Request, mode: str = "normal"):
    require_admin(request)
    if not EVAL_CASES_FILE.exists():
        raise HTTPException(status_code=404, detail="eval_cases.json introuvable")
    cases = json.loads(EVAL_CASES_FILE.read_text())

    if mode == "batch":
        import anthropic as _anthropic
        batch_requests = []
        for case in cases:
            context = get_demographic_context(case["specialite"], case["ville"])
            user_prompt = build_diagnostic_prompt(
                reponses=case["reponses"],
                texte_libre=case.get("texte_libre", ""),
                specialite=case["specialite"],
                ville=case["ville"],
                catalogue=""
            )
            if context:
                user_prompt = context + "\n\n" + user_prompt
            batch_requests.append({
                "custom_id": case["id"],
                "params": {
                    "model": ANTHROPIC_MODEL,
                    "max_tokens": 4000,
                    "system": SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": user_prompt}]
                }
            })
        batch = anthropic_client.beta.messages.batches.create(requests=batch_requests)
        records = []
        if EVAL_BATCHES_FILE.exists():
            records = json.loads(EVAL_BATCHES_FILE.read_text())
        records.append({
            "batch_id": batch.id,
            "case_count": len(cases),
            "launched_at": datetime.now().isoformat(),
            "status": "in_progress"
        })
        EVAL_BATCHES_FILE.write_text(json.dumps(records, ensure_ascii=False, indent=2))
        return {"batch_id": batch.id, "total": len(cases)}

    else:
        job_id = str(_uuid.uuid4())[:8]
        t = threading.Thread(target=_run_eval_thread, args=(job_id, cases), daemon=True)
        t.start()
        return {"job_id": job_id, "total": len(cases)}


@app.post("/api/admin/eval/run")
async def eval_run(request: Request, mode: str = "normal"):
    require_admin(request)
    if not EVAL_CASES_FILE.exists():
        raise HTTPException(status_code=404, detail="eval_cases.json introuvable")
    cases = json.loads(EVAL_CASES_FILE.read_text())

    if mode == "batch":
        batch_requests = []
        for case in cases:
            context = get_demographic_context(case["specialite"], case["ville"])
            user_prompt = build_diagnostic_prompt(
                reponses=case["reponses"],
                texte_libre=case.get("texte_libre", ""),
                specialite=case["specialite"],
                ville=case["ville"],
                catalogue=""
            )
            if context:
                user_prompt = context + "\n\n" + user_prompt
            batch_requests.append({
                "custom_id": case["id"],
                "params": {
                    "model": ANTHROPIC_MODEL,
                    "max_tokens": 4000,
                    "system": SYSTEM_PROMPT,
                    "messages": [{"role": "user", "content": user_prompt}]
                }
            })
        batch = anthropic_client.beta.messages.batches.create(requests=batch_requests)
        records = []
        if EVAL_BATCHES_FILE.exists():
            records = json.loads(EVAL_BATCHES_FILE.read_text())
        records.append({
            "batch_id": batch.id,
            "case_count": len(cases),
            "launched_at": datetime.now().isoformat(),
            "status": "in_progress"
        })
        EVAL_BATCHES_FILE.write_text(json.dumps(records, ensure_ascii=False, indent=2))
        return {"batch_id": batch.id, "total": len(cases)}
    else:
        job_id = str(_uuid.uuid4())[:8]
        t = threading.Thread(target=_run_eval_thread, args=(job_id, cases), daemon=True)
        t.start()
        return {"job_id": job_id, "total": len(cases)}


@app.get("/api/admin/eval/status/{job_id}")
async def eval_status(job_id: str, request: Request):
    require_admin(request)
    job = _eval_jobs.get(job_id, {"done": 0, "total": 0, "status": "unknown"})
    return job


@app.get("/api/admin/eval/results")
async def eval_results(request: Request):
    require_admin(request)
    if not EVAL_RESULTS_FILE.exists():
        return []
    return json.loads(EVAL_RESULTS_FILE.read_text())


@app.get("/api/admin/eval/batches")
async def eval_batches(request: Request):
    require_admin(request)
    if not EVAL_BATCHES_FILE.exists():
        return []
    return json.loads(EVAL_BATCHES_FILE.read_text())


@app.get("/api/admin/eval/batch-status/{batch_id}")
async def eval_batch_status(batch_id: str, request: Request):
    require_admin(request)
    batch = anthropic_client.beta.messages.batches.retrieve(batch_id)
    counts = batch.request_counts
    # Mettre à jour le fichier batches
    if EVAL_BATCHES_FILE.exists():
        records = json.loads(EVAL_BATCHES_FILE.read_text())
        for r in records:
            if r["batch_id"] == batch_id:
                r["status"] = batch.processing_status
        EVAL_BATCHES_FILE.write_text(json.dumps(records, ensure_ascii=False, indent=2))
    return {
        "status": batch.processing_status,
        "succeeded": counts.succeeded,
        "errored": counts.errored,
        "processing": counts.processing
    }


@app.post("/api/admin/eval/batch-get/{batch_id}")
async def eval_batch_get(batch_id: str, request: Request):
    require_admin(request)
    batch = anthropic_client.beta.messages.batches.retrieve(batch_id)
    if batch.processing_status != "ended":
        raise HTTPException(status_code=400, detail="Batch pas encore terminé")

    cases_by_id = {}
    if EVAL_CASES_FILE.exists():
        for c in json.loads(EVAL_CASES_FILE.read_text()):
            cases_by_id[c["id"]] = c

    results = {}
    if EVAL_RESULTS_FILE.exists():
        for r in json.loads(EVAL_RESULTS_FILE.read_text()):
            results[r["id"]] = r

    import re
    for result in anthropic_client.beta.messages.batches.results(batch_id):
        cid = result.custom_id
        case = cases_by_id.get(cid, {})
        if result.result.type == "succeeded":
            raw = result.result.message.content[0].text
            try:
                bilan = json.loads(raw.strip())
            except Exception:
                match = re.search(r'\{.*\}', raw, re.DOTALL)
                bilan = json.loads(match.group()) if match else {"erreur": "JSON invalide"}
            status = "ok" if "score_global" in bilan else "erreur"
        else:
            bilan, status = {}, "erreur"

        results[cid] = {
            "id": cid, "label": case.get("label", ""),
            "specialite": case.get("specialite", ""), "ville": case.get("ville", ""),
            "texte_libre": case.get("texte_libre", ""),
            "bilan": bilan, "status": status, "mode": "batch",
            "timestamp": datetime.now().isoformat()
        }

    EVAL_RESULTS_FILE.write_text(json.dumps(list(results.values()), ensure_ascii=False, indent=2))
    # Mettre à jour statut batch
    if EVAL_BATCHES_FILE.exists():
        records = json.loads(EVAL_BATCHES_FILE.read_text())
        for r in records:
            if r["batch_id"] == batch_id:
                r["status"] = "ended"
        EVAL_BATCHES_FILE.write_text(json.dumps(records, ensure_ascii=False, indent=2))
    return {"retrieved": len(results)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
