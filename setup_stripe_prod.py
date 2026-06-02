"""
Bigdoc — Script de migration Stripe TEST → PROD
Usage : python3 setup_stripe_prod.py sk_live_TACLEF
"""
import sys, sqlite3, os

if len(sys.argv) < 2:
    print("Usage: python3 setup_stripe_prod.py sk_live_...")
    sys.exit(1)

import stripe
stripe.api_key = sys.argv[1]

if not stripe.api_key.startswith("sk_live_"):
    print("❌ Clé non live — passe bien sk_live_...")
    sys.exit(1)

print("🔑 Clé live détectée")

# 1. Créer les produits Stripe
produits = [
    {"nom": "Sérénité",        "prix": 9000,  "type": "abonnement", "db_nom": "serenite"},
    {"nom": "Confort",         "prix": 25000, "type": "abonnement", "db_nom": "confort"},
    {"nom": "Cabinet libéré",  "prix": 59000, "type": "abonnement", "db_nom": "libere"},
]

price_ids = {}
for p in produits:
    prod = stripe.Product.create(name=p["nom"], description=f"Bigdoc — {p['nom']}")
    price = stripe.Price.create(
        product=prod.id,
        unit_amount=p["prix"],
        currency="eur",
        recurring={"interval": "month"},
    )
    price_ids[p["db_nom"]] = price.id
    print(f"✅ {p['nom']} → {price.id}")

# 2. Mettre à jour la base SQLite
db_path = os.getenv("DATABASE_PATH", "/app/bigdoc.db")
if not os.path.exists(db_path):
    db_path = "bigdoc.db"

print(f"\n📦 Base de données : {db_path}")
conn = sqlite3.connect(db_path)

for db_nom, price_id in price_ids.items():
    conn.execute("""
        UPDATE products SET stripe_price_id = ?
        WHERE LOWER(nom) LIKE ?
    """, (price_id, f"%{db_nom.replace('_',' ')}%"))
    print(f"   DB updated: {db_nom} → {price_id}")

conn.commit()
conn.close()

print("\n✅ Terminé — mets à jour le .env avec sk_live_... et STRIPE_WEBHOOK_SECRET")
print("   docker compose restart")
