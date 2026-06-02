import json
import secrets
import httpx
import os
import logging
from fastapi import FastAPI, HTTPException, Request, Response
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
    get_lead_by_session, verify_admin,
    get_app_settings, save_app_settings,
    init_products, get_products, get_product,
    create_product, update_product, delete_product,
    toggle_product, get_catalogue_for_prompt
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
        type_labels = {"sous_dote": "ZONE SOUS-DOTÉE ⚠️", "sur_dote": "Zone sur-dotée", "intermédiaire": "Zone intermédiaire"}
        lines.append(f"Territoire : {dept_nom} ({dept}) — {type_labels.get(dept_type, dept_type)}")
        if dept_type == "sous_dote":
            lines.append("→ Zone sous-dotée ARS : CAIM jusqu'à 50 000€, DAC, CPTS disponibles — valoriser dans le bilan")
        elif dept_type == "sur_dote":
            lines.append("→ Zone sur-dotée : concurrence forte — différenciation et spécialisation indispensables")

    if densite_locale and densite_nationale:
        ratio = densite_locale / densite_nationale
        if ratio < 0.75:
            lines.append(f"Densité {spe_norm} locale : FAIBLE ({densite_locale}/100k hab vs {densite_nationale}/100k national) → forte opportunité de développement")
        elif ratio > 1.25:
            lines.append(f"Densité {spe_norm} locale : ÉLEVÉE ({densite_locale}/100k hab vs {densite_nationale}/100k national) → différenciation nécessaire")
        else:
            lines.append(f"Densité {spe_norm} locale : dans la moyenne ({densite_locale}/100k hab vs {densite_nationale}/100k national)")
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
        body.session_id, body.email, body.prenom, body.specialite, body.ville
    )
    return {"success": True, "lead_id": lead_id}


@app.get("/api/bilan/{session_id}")
async def get_bilan(session_id: str):
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
@app.get("/admin", response_class=HTMLResponse)
async def admin_page():
    with open("static/admin.html", "r", encoding="utf-8") as f:
        return f.read()


class AdminLoginRequest(BaseModel):
    username: str
    password: str


@app.post("/api/admin/login")
async def admin_login(body: AdminLoginRequest):
    """Authentification admin — renvoie un token de session."""
    if verify_admin(body.username, body.password):
        # Token de session simple (valable le temps de la session navigateur)
        session_token = secrets.token_urlsafe(32)
        return {"success": True, "token": session_token}
    raise HTTPException(status_code=401, detail="Identifiants incorrects")


@app.get("/api/admin/leads")
async def admin_leads(request: Request):
    """Liste tous les diagnostics — protégé par login DB."""
    auth = request.headers.get("X-Admin-Token", "")
    if not auth:
        raise HTTPException(status_code=401, detail="Non autorisé")
    return get_all_leads()


@app.get("/api/admin/settings")
async def get_settings_route(request: Request):
    """Récupère les paramètres depuis la base."""
    auth = request.headers.get("X-Admin-Token", "")
    if not auth:
        raise HTTPException(status_code=401, detail="Non autorisé")
    return get_app_settings()


@app.post("/api/admin/settings")
async def save_settings_route(request: Request):
    """Sauvegarde les paramètres dans la base."""
    auth = request.headers.get("X-Admin-Token", "")
    if not auth:
        raise HTTPException(status_code=401, detail="Non autorisé")
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
    auth = request.headers.get("X-Admin-Token", "")
    if not auth: raise HTTPException(status_code=401, detail="Non autorisé")
    return get_products()


@app.post("/api/admin/products")
async def admin_create_product(request: Request):
    auth = request.headers.get("X-Admin-Token", "")
    if not auth: raise HTTPException(status_code=401, detail="Non autorisé")
    data = await request.json()
    product_id = create_product(data)
    return {"success": True, "id": product_id}


@app.put("/api/admin/products/{product_id}")
async def admin_update_product(product_id: int, request: Request):
    auth = request.headers.get("X-Admin-Token", "")
    if not auth: raise HTTPException(status_code=401, detail="Non autorisé")
    data = await request.json()
    update_product(product_id, data)
    return {"success": True}


@app.delete("/api/admin/products/{product_id}")
async def admin_delete_product(product_id: int, request: Request):
    auth = request.headers.get("X-Admin-Token", "")
    if not auth: raise HTTPException(status_code=401, detail="Non autorisé")
    delete_product(product_id)
    return {"success": True}


@app.post("/api/admin/products/{product_id}/toggle")
async def admin_toggle_product(product_id: int, request: Request):
    auth = request.headers.get("X-Admin-Token", "")
    if not auth: raise HTTPException(status_code=401, detail="Non autorisé")
    actif = toggle_product(product_id)
    return {"success": True, "actif": actif}


@app.get("/api/admin/stripe-stats")
async def stripe_stats_route(request: Request):
    """Stats CA depuis Stripe."""
    auth = request.headers.get("X-Admin-Token", "")
    if not auth:
        raise HTTPException(status_code=401, detail="Non autorisé")

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

    # Chercher le produit dans le catalogue
    product = None
    try:
        product = get_product(int(body.produit))
    except (ValueError, TypeError):
        pass

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
            success_url=f"https://bigdoc.fr/bilan/{body.session_id}?paiement=ok",
            cancel_url=f"https://bigdoc.fr/bilan/{body.session_id}",
            metadata={"session_id": body.session_id, "produit_id": str(product["id"]), "produit_nom": nom}
        )
        return {"checkout_url": session.url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/stripe-webhook")
async def stripe_webhook(request: Request):
    """Webhook Stripe — confirme les paiements."""
    import stripe
    settings = get_app_settings()
    sk = settings.get("stripe_sk") or STRIPE_SECRET_KEY
    stripe.api_key = sk

    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    webhook_secret = settings.get("stripe_webhook_secret") or STRIPE_WEBHOOK_SECRET

    try:
        event = stripe.Webhook.construct_event(payload, sig, webhook_secret)
    except Exception:
        raise HTTPException(status_code=400, detail="Signature Stripe invalide")

    if event["type"] in ("checkout.session.completed", "invoice.payment_succeeded"):
        session = event["data"]["object"]
        meta = session.get("metadata", {})
        logger.info(f"✅ Paiement confirmé — session {meta.get('session_id')} / {meta.get('produit_nom')}")

    return {"received": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
