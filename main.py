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
    TURNSTILE_SECRET, SYSTEM_PROMPT, SYSTEM_PROMPT_CHAT_REACTION, QUESTIONNAIRE
)
from database import (
    init_db, save_diagnostic, save_lead, get_diagnostic,
    create_partage_token, get_diagnostic_by_token,
    delete_lead_data, get_stats
)

# Modèle Anthropic — configurable dans .env
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")

logger = logging.getLogger("bigdoc")

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
    reponses: dict          # {question_id: valeur}
    texte_libre: str = ""
    turnstile_token: str = ""


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


def build_diagnostic_prompt(reponses: dict, texte_libre: str) -> str:
    """Construit le prompt utilisateur à partir des réponses."""
    lines = ["Voici les réponses du médecin au questionnaire de diagnostic :\n"]

    labels = {
        "phase": "Phase du cabinet",
        "admin": "Charge administrative",
        "materiel": "État du matériel et stocks",
        "teleconsult": "Situation téléconsultation",
        "compta": "Comptabilité et trésorerie",
        "charge": "Charge mentale hors soins",
        "financement": "Projets de financement",
        "developpement": "Vision de développement",
    }

    # Map option values to readable labels
    option_map = {}
    for q in QUESTIONNAIRE:
        option_map[q["id"]] = {opt["value"]: opt["label"] for opt in q["options"]}

    for qid, val in reponses.items():
        label = labels.get(qid, qid)
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

    # Construire le prompt
    user_prompt = build_diagnostic_prompt(body.reponses, body.texte_libre)

    # Appel Claude Sonnet
    try:
        message = anthropic_client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=2000,
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
        raise HTTPException(status_code=500, detail=f"Erreur parsing bilan : {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur IA : {str(e)}")

    # Sauvegarder
    diagnostic_id = save_diagnostic(body.session_id, bilan, body.reponses, body.texte_libre)

    # Générer token de partage
    share_token = secrets.token_urlsafe(16)
    if diagnostic_id:
        create_partage_token(diagnostic_id, share_token)

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
# STRIPE (structure prête, à compléter avec vrais price_id)
# ─────────────────────────────────────────
class CheckoutRequest(BaseModel):
    session_id: str
    produit: str   # "plan_action"|"serenitee"|"confort"|"cabinet_libere"|"installation"
    email: str = ""


STRIPE_PRODUCTS = {
    "plan_action":     {"nom": "Plan d'action personnalisé", "prix_cents": 3500,  "mode": "payment"},
    "audit_express":   {"nom": "Audit express",              "prix_cents": 3900,  "mode": "payment"},
    "installation":    {"nom": "Installation clé en main",   "prix_cents": 25000, "mode": "payment"},
    "business_plan":   {"nom": "Business plan & dossier bancaire", "prix_cents": 25000, "mode": "payment"},
    "serenite":        {"nom": "Abonnement Sérénité",        "prix_cents": 9000,  "mode": "subscription"},
    "confort":         {"nom": "Abonnement Confort",         "prix_cents": 25000, "mode": "subscription"},
    "cabinet_libere":  {"nom": "Abonnement Cabinet libéré",  "prix_cents": 59000, "mode": "subscription"},
}


@app.post("/api/create-checkout")
async def create_checkout(body: CheckoutRequest):
    """Crée une session Stripe Checkout."""
    import stripe
    stripe.api_key = STRIPE_SECRET_KEY

    produit = STRIPE_PRODUCTS.get(body.produit)
    if not produit:
        raise HTTPException(status_code=400, detail="Produit inconnu")

    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            mode=produit["mode"],
            line_items=[{
                "price_data": {
                    "currency": "eur",
                    "product_data": {"name": produit["nom"]},
                    "unit_amount": produit["prix_cents"],
                    **({"recurring": {"interval": "month"}} if produit["mode"] == "subscription" else {})
                },
                "quantity": 1,
            }],
            customer_email=body.email or None,
            success_url=f"https://bigdoc.fr/bilan/{body.session_id}?paiement=ok",
            cancel_url=f"https://bigdoc.fr/bilan/{body.session_id}",
            metadata={"session_id": body.session_id, "produit": body.produit}
        )
        return {"checkout_url": session.url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/stripe-webhook")
async def stripe_webhook(request: Request):
    """Webhook Stripe — confirme les paiements."""
    import stripe
    stripe.api_key = STRIPE_SECRET_KEY

    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except Exception:
        raise HTTPException(status_code=400, detail="Signature Stripe invalide")

    if event["type"] in ("checkout.session.completed", "invoice.payment_succeeded"):
        session = event["data"]["object"]
        meta = session.get("metadata", {})
        # TODO: déclencher génération bilan détaillé / email de confirmation
        print(f"✅ Paiement confirmé — session {meta.get('session_id')} / {meta.get('produit')}")

    return {"received": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
