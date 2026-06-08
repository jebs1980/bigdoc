import sqlite3
import json
from datetime import datetime
from config import DATABASE_PATH


def get_connection():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS products (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            nom           TEXT NOT NULL,
            description   TEXT,
            prix_cents    INTEGER NOT NULL DEFAULT 0,
            type          TEXT NOT NULL DEFAULT 'unique',
            bouton        TEXT NOT NULL DEFAULT 'rdv',
            stripe_price_id TEXT,
            actif         INTEGER NOT NULL DEFAULT 1,
            ordre         INTEGER NOT NULL DEFAULT 0,
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS admin_users (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            username        TEXT UNIQUE NOT NULL,
            email           TEXT UNIQUE NOT NULL,
            password_hash   TEXT NOT NULL,
            reset_token     TEXT,
            reset_expires   TIMESTAMP,
            login_attempts  INTEGER DEFAULT 0,
            locked_until    TIMESTAMP,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_login      TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS admin_sessions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            token       TEXT UNIQUE NOT NULL,
            admin_id    INTEGER REFERENCES admin_users(id),
            expires_at  TIMESTAMP NOT NULL,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS leads (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            email           TEXT NOT NULL,
            prenom          TEXT,
            nom             TEXT,
            specialite      TEXT,
            ville           TEXT,
            rpps            TEXT,
            status          TEXT DEFAULT 'lead',
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            rgpd_consent    INTEGER DEFAULT 1,
            rgpd_date       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            source          TEXT DEFAULT 'diagnostic'
        );

        CREATE TABLE IF NOT EXISTS lead_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id     INTEGER NOT NULL REFERENCES leads(id),
            type        TEXT NOT NULL,
            content     TEXT,
            meta        TEXT,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS diagnostics (
            id                     INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id             TEXT UNIQUE NOT NULL,
            lead_id                INTEGER REFERENCES leads(id),
            phase                  TEXT,
            score_global           INTEGER,
            niveau                 TEXT,
            dim_administration     INTEGER,
            dim_achats_materiel    INTEGER,
            dim_informatique       INTEGER,
            dim_comptabilite       INTEGER,
            dim_charge_mentale     INTEGER,
            dim_financement        INTEGER,
            dim_developpement      INTEGER,
            heures_perdues_semaine REAL,
            euros_evitables_an     REAL,
            recommandation_palier  TEXT,
            recommandation_tarif   TEXT,
            bilan_json             TEXT,
            texte_libre            TEXT,
            reponses_json          TEXT,
            created_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            bilan_envoye           INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS partages (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            diagnostic_id INTEGER REFERENCES diagnostics(id),
            token         TEXT UNIQUE NOT NULL,
            consulte      INTEGER DEFAULT 0,
            consulte_at   TIMESTAMP,
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS paiements (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            diagnostic_id   INTEGER REFERENCES diagnostics(id),
            stripe_session  TEXT UNIQUE,
            stripe_intent   TEXT,
            montant_cents   INTEGER,
            service         TEXT,
            statut          TEXT DEFAULT 'pending',
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            paid_at         TIMESTAMP
        );
    """)

    conn.commit()
    conn.close()


def save_diagnostic(session_id: str, bilan: dict, reponses: dict, texte_libre: str = "") -> int:
    conn = get_connection()
    dims = bilan.get("dimensions", {})
    reco = bilan.get("recommandation_principale") or {}

    conn.execute("""
        INSERT INTO diagnostics (
            session_id, phase, score_global, niveau,
            dim_administration, dim_achats_materiel, dim_informatique,
            dim_comptabilite, dim_charge_mentale, dim_financement, dim_developpement,
            heures_perdues_semaine, euros_evitables_an,
            recommandation_palier, recommandation_tarif,
            bilan_json, texte_libre, reponses_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(session_id) DO UPDATE SET
            bilan_json = excluded.bilan_json,
            score_global = excluded.score_global
    """, (
        session_id,
        bilan.get("phase"),
        bilan.get("score_global"),
        bilan.get("niveau"),
        (dims.get("administration") or {}).get("score"),
        (dims.get("achats_materiel") or {}).get("score"),
        (dims.get("informatique_teleconsult") or dims.get("informatique") or {}).get("score"),
        (dims.get("comptabilite_finances") or dims.get("comptabilite") or {}).get("score"),
        (dims.get("charge_mentale") or {}).get("score"),
        (dims.get("financement_investissements") or dims.get("financement") or {}).get("score"),
        (dims.get("developpement_croissance") or dims.get("developpement") or {}).get("score"),
        bilan.get("heures_perdues_semaine"),
        bilan.get("euros_evitables_an"),
        reco.get("palier"),
        reco.get("tarif"),
        json.dumps(bilan, ensure_ascii=False),
        texte_libre,
        json.dumps(reponses, ensure_ascii=False),
    ))

    row = conn.execute("SELECT id FROM diagnostics WHERE session_id = ?", (session_id,)).fetchone()
    conn.commit()
    conn.close()
    return row["id"] if row else 0


def save_lead(session_id: str, email: str, prenom: str = "", specialite: str = "", ville: str = "", nom: str = "") -> int:
    conn = get_connection()

    # Ajouter colonne nom si elle n'existe pas (migration)
    try:
        conn.execute("ALTER TABLE leads ADD COLUMN nom TEXT")
        conn.commit()
    except Exception:
        pass

    conn.execute("""
        INSERT INTO leads (email, prenom, nom, specialite, ville)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT DO NOTHING
    """, (email.lower().strip(), prenom, nom, specialite, ville))

    lead = conn.execute("SELECT id FROM leads WHERE email = ?", (email.lower().strip(),)).fetchone()
    lead_id = lead["id"] if lead else 0

    # Link to diagnostic
    conn.execute(
        "UPDATE diagnostics SET lead_id = ? WHERE session_id = ?",
        (lead_id, session_id)
    )

    conn.commit()
    conn.close()
    return lead_id


def get_diagnostic(session_id: str) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM diagnostics WHERE session_id = ?", (session_id,)
    ).fetchone()
    conn.close()
    if row and row["bilan_json"]:
        return json.loads(row["bilan_json"])
    return None


def create_partage_token(diagnostic_id: int, token: str):
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO partages (diagnostic_id, token) VALUES (?, ?)",
        (diagnostic_id, token)
    )
    conn.commit()
    conn.close()


def get_diagnostic_by_token(token: str) -> dict | None:
    conn = get_connection()
    row = conn.execute("""
        SELECT d.bilan_json, d.session_id FROM partages p
        JOIN diagnostics d ON d.id = p.diagnostic_id
        WHERE p.token = ?
    """, (token,)).fetchone()

    if row:
        conn.execute(
            "UPDATE partages SET consulte=1, consulte_at=CURRENT_TIMESTAMP WHERE token=?",
            (token,)
        )
        conn.commit()

    conn.close()
    return json.loads(row["bilan_json"]) if row and row["bilan_json"] else None


def delete_lead_data(email: str) -> bool:
    """RGPD — suppression complète des données d'un lead."""
    conn = get_connection()
    lead = conn.execute("SELECT id FROM leads WHERE email = ?", (email.lower().strip(),)).fetchone()
    if not lead:
        conn.close()
        return False

    lead_id = lead["id"]
    # Anonymize diagnostics (keep stats, delete personal data)
    conn.execute(
        "UPDATE diagnostics SET lead_id = NULL, texte_libre = '[supprimé]' WHERE lead_id = ?",
        (lead_id,)
    )
    conn.execute("DELETE FROM leads WHERE id = ?", (lead_id,))
    conn.commit()
    conn.close()
    return True


def _hash_password(password: str, salt: str = None) -> tuple:
    """Hash un mot de passe avec sel aléatoire."""
    import hashlib, os
    if salt is None:
        salt = os.urandom(32).hex()
    h = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 200_000).hex()
    return salt, h

def is_admin_setup_done() -> bool:
    """Retourne True si au moins un admin est configuré."""
    conn = get_connection()
    row = conn.execute("SELECT COUNT(*) as n FROM admin_users").fetchone()
    conn.close()
    return row["n"] > 0 if row else False

def setup_admin(email: str, username: str, password: str) -> bool:
    """Configure le premier compte admin. Retourne False si déjà fait."""
    if is_admin_setup_done():
        return False
    salt, h = _hash_password(password)
    conn = get_connection()
    conn.execute(
        "INSERT INTO admin_users (username, email, password_hash) VALUES (?,?,?)",
        (username, email, f"{salt}${h}")
    )
    conn.commit()
    conn.close()
    return True

def init_admin():
    """Migration DB uniquement — ne crée plus de compte par défaut."""
    conn = get_connection()
    for col, defval in [
        ("email", "TEXT"),
        ("reset_token", "TEXT"),
        ("reset_expires", "TIMESTAMP"),
        ("login_attempts", "INTEGER DEFAULT 0"),
        ("locked_until", "TIMESTAMP"),
        ("last_login", "TIMESTAMP")
    ]:
        try:
            conn.execute(f"ALTER TABLE admin_users ADD COLUMN {col} {defval}")
        except Exception:
            pass
    conn.execute("""
        CREATE TABLE IF NOT EXISTS admin_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT UNIQUE NOT NULL,
            admin_id INTEGER REFERENCES admin_users(id),
            expires_at TIMESTAMP NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def verify_admin(username: str, password: str) -> dict | None:
    """Vérifie identifiants. Retourne l'admin ou None. Gère blocage."""
    from datetime import datetime, timedelta
    import hashlib, os

    # Priorité .env
    env_user = os.getenv("ADMIN_USERNAME", "admin")
    env_pass = os.getenv("ADMIN_PASSWORD", "")
    if env_pass and username == env_user and password == env_pass:
        return {"id": 0, "username": username, "email": ""}

    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM admin_users WHERE username=? OR email=?", (username, username)
    ).fetchone()

    if not row:
        conn.close()
        return None

    admin = dict(row)

    # Vérifier blocage
    if admin.get("locked_until"):
        locked = datetime.fromisoformat(admin["locked_until"])
        if datetime.now() < locked:
            conn.close()
            remaining = int((locked - datetime.now()).total_seconds() / 60)
            raise ValueError(f"Compte bloqué — réessayez dans {remaining} min ou réinitialisez votre mot de passe")

    # Vérifier mot de passe
    try:
        salt, hashed = admin["password_hash"].split("$")
        check = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 200_000).hex()
        ok = check == hashed
    except Exception:
        ok = False

    if ok:
        conn.execute("UPDATE admin_users SET login_attempts=0, locked_until=NULL, last_login=? WHERE id=?",
                     (datetime.now().isoformat(), admin["id"]))
        conn.commit()
        conn.close()
        return admin
    else:
        attempts = (admin.get("login_attempts") or 0) + 1
        locked_until = None
        if attempts >= 5:
            locked_until = (datetime.now() + timedelta(minutes=15)).isoformat()
        conn.execute("UPDATE admin_users SET login_attempts=?, locked_until=? WHERE id=?",
                     (attempts, locked_until, admin["id"]))
        conn.commit()
        conn.close()
        if locked_until:
            raise ValueError("Trop de tentatives — compte bloqué 15 min. Réinitialisez votre mot de passe.")
        return None

def create_admin_session(admin_id: int) -> str:
    """Crée un token de session valable 24h."""
    import secrets
    from datetime import datetime, timedelta
    token = secrets.token_urlsafe(48)
    expires = (datetime.now() + timedelta(hours=24)).isoformat()
    conn = get_connection()
    conn.execute("DELETE FROM admin_sessions WHERE expires_at < ?", (datetime.now().isoformat(),))
    conn.execute("INSERT INTO admin_sessions (token, admin_id, expires_at) VALUES (?,?,?)",
                 (token, admin_id, expires))
    conn.commit()
    conn.close()
    return token

def verify_admin_session(token: str) -> dict | None:
    """Vérifie un token de session. Retourne l'admin ou None."""
    from datetime import datetime
    if not token:
        return None
    conn = get_connection()
    row = conn.execute("""
        SELECT u.* FROM admin_sessions s
        JOIN admin_users u ON u.id = s.admin_id
        WHERE s.token=? AND s.expires_at > ?
    """, (token, datetime.now().isoformat())).fetchone()
    conn.close()
    return dict(row) if row else None

def delete_admin_session(token: str):
    """Supprime une session (logout)."""
    conn = get_connection()
    conn.execute("DELETE FROM admin_sessions WHERE token=?", (token,))
    conn.commit()
    conn.close()

def create_reset_token(email: str) -> str | None:
    """Génère un token de reset pour l'email donné."""
    import secrets
    from datetime import datetime, timedelta
    conn = get_connection()
    row = conn.execute("SELECT id FROM admin_users WHERE email=?", (email,)).fetchone()
    if not row:
        conn.close()
        return None
    token = secrets.token_urlsafe(48)
    expires = (datetime.now() + timedelta(hours=1)).isoformat()
    conn.execute("UPDATE admin_users SET reset_token=?, reset_expires=?, login_attempts=0, locked_until=NULL WHERE email=?",
                 (token, expires, email))
    conn.commit()
    conn.close()
    return token

def reset_admin_password(token: str, new_password: str) -> bool:
    """Réinitialise le mot de passe via token. Retourne True si succès."""
    from datetime import datetime
    conn = get_connection()
    row = conn.execute(
        "SELECT id FROM admin_users WHERE reset_token=? AND reset_expires > ?",
        (token, datetime.now().isoformat())
    ).fetchone()
    if not row:
        conn.close()
        return False
    salt, h = _hash_password(new_password)
    conn.execute("""
        UPDATE admin_users
        SET password_hash=?, reset_token=NULL, reset_expires=NULL,
            login_attempts=0, locked_until=NULL
        WHERE id=?
    """, (f"{salt}${h}", row["id"]))
    conn.commit()
    conn.close()
    return True


def get_lead_by_session(session_id: str) -> dict | None:
    """Récupère les infos du lead lié à une session."""
    conn = get_connection()
    row = conn.execute("""
        SELECT l.email, l.prenom, l.specialite, l.ville
        FROM diagnostics d
        LEFT JOIN leads l ON l.id = d.lead_id
        WHERE d.session_id = ?
    """, (session_id,)).fetchone()
    conn.close()
    if row:
        return dict(row)
    return None


def get_all_leads() -> list:
    """Retourne tous les diagnostics pour le back office admin."""
    conn = get_connection()
    _migrate_lead_crm(conn)  # S'assure que les colonnes CRM existent
    rows = conn.execute("""
        SELECT
            l.id,
            d.session_id,
            d.created_at as date,
            l.email,
            l.prenom,
            l.nom,
            l.specialite,
            l.ville,
            l.status,
            d.phase,
            d.score_global,
            d.heures_perdues_semaine,
            d.euros_evitables_an,
            d.recommandation_palier,
            d.recommandation_tarif
        FROM diagnostics d
        LEFT JOIN leads l ON l.id = d.lead_id
        WHERE d.score_global IS NOT NULL AND l.id IS NOT NULL
        ORDER BY d.created_at DESC
        LIMIT 500
    """).fetchall()
    conn.close()
    return [dict(row) for row in rows]


PRODUITS_DEFAUT = [
    {"nom": "Diagnostic confort",        "description": "Bilan complet 7 dimensions",                      "prix_cents": 0,     "type": "unique",      "bouton": "rdv",    "ordre": 1},
    {"nom": "Plan d'action personnalisé","description": "Feuille de route priorisée pour votre cabinet",    "prix_cents": 3500,  "type": "unique",      "bouton": "payer",  "ordre": 2},
    {"nom": "Audit express",             "description": "Relecture facture, devis ou document clé",         "prix_cents": 3900,  "type": "unique",      "bouton": "payer",  "ordre": 3},
    {"nom": "Sourcing matériel",         "description": "Pièce introuvable, fabrication sur mesure",        "prix_cents": 25000, "type": "unique",      "bouton": "rdv",    "ordre": 4},
    {"nom": "Intégration téléconsultation","description":"Plateforme agréée, renvoi IP, formation complète", "prix_cents": 25000, "type": "unique",      "bouton": "rdv",    "ordre": 5},
    {"nom": "Mise en conformité téléconsult","description":"Migration depuis Zoom/Teams vers plateforme légale","prix_cents":25000,"type":"unique",      "bouton": "rdv",    "ordre": 6},
    {"nom": "Business plan & dossier bancaire","description":"Prévisionnel médecin, dossier banque",        "prix_cents": 25000, "type": "unique",      "bouton": "rdv",    "ordre": 7},
    {"nom": "Installation cabinet clé en main","description":"De la recherche du local aux formalités",     "prix_cents": 100000,"type": "unique",      "bouton": "rdv",    "ordre": 8},
    {"nom": "Abonnement Sérénité",       "description": "Suivi mensuel, questions illimitées",              "prix_cents": 9000,  "type": "abonnement",  "bouton": "rdv",    "ordre": 9},
    {"nom": "Abonnement Confort",        "description": "Gestion courante + support + comptable",           "prix_cents": 25000, "type": "abonnement",  "bouton": "rdv",    "ordre": 10},
    {"nom": "Abonnement Cabinet libéré", "description": "Délégation totale — RMS gère, vous soignez",      "prix_cents": 59000, "type": "abonnement",  "bouton": "rdv",    "ordre": 11},
]


def init_products():
    """Insère les produits par défaut si la table est vide."""
    conn = get_connection()
    count = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    if count == 0:
        for p in PRODUITS_DEFAUT:
            conn.execute("""
                INSERT INTO products (nom, description, prix_cents, type, bouton, ordre)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (p["nom"], p["description"], p["prix_cents"], p["type"], p["bouton"], p["ordre"]))
        conn.commit()
    conn.close()


def get_products(actif_only: bool = False) -> list:
    conn = get_connection()
    q = "SELECT * FROM products"
    if actif_only:
        q += " WHERE actif = 1"
    q += " ORDER BY ordre, id"
    rows = conn.execute(q).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_product(product_id: int) -> dict | None:
    conn = get_connection()
    row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def create_product(data: dict) -> int:
    conn = get_connection()
    cursor = conn.execute("""
        INSERT INTO products (nom, description, prix_cents, type, bouton, stripe_price_id, actif, ordre)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data.get("nom", ""),
        data.get("description", ""),
        int(data.get("prix_cents", 0)),
        data.get("type", "unique"),
        data.get("bouton", "rdv"),
        data.get("stripe_price_id", ""),
        1 if data.get("actif", True) else 0,
        int(data.get("ordre", 99)),
    ))
    product_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return product_id


def update_product(product_id: int, data: dict):
    conn = get_connection()
    conn.execute("""
        UPDATE products SET
            nom = ?, description = ?, prix_cents = ?, type = ?,
            bouton = ?, stripe_price_id = ?, actif = ?, ordre = ?
        WHERE id = ?
    """, (
        data.get("nom", ""),
        data.get("description", ""),
        int(data.get("prix_cents", 0)),
        data.get("type", "unique"),
        data.get("bouton", "rdv"),
        data.get("stripe_price_id", ""),
        1 if data.get("actif", True) else 0,
        int(data.get("ordre", 99)),
        product_id,
    ))
    conn.commit()
    conn.close()


def delete_product(product_id: int):
    conn = get_connection()
    conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
    conn.commit()
    conn.close()


def toggle_product(product_id: int) -> bool:
    conn = get_connection()
    row = conn.execute("SELECT actif FROM products WHERE id = ?", (product_id,)).fetchone()
    if not row:
        conn.close()
        return False
    new_actif = 0 if row["actif"] else 1
    conn.execute("UPDATE products SET actif = ? WHERE id = ?", (new_actif, product_id))
    conn.commit()
    conn.close()
    return bool(new_actif)


def get_catalogue_for_prompt() -> str:
    """Génère le catalogue produits formaté pour le system prompt."""
    products = get_products(actif_only=True)
    lines = []
    for p in products:
        prix = f"{p['prix_cents'] // 100}€" if p['prix_cents'] > 0 else "Gratuit"
        if p['type'] == 'abonnement':
            prix += "/mois"
        lines.append(f"• {p['nom']} — {prix} ({p['type']}) : {p['description']}")
    return "\n".join(lines)


SETTINGS_DEFAULTS = {    "stripe_mode": "test",
    "stripe_pk": "",
    "stripe_sk": "",
    "prix_plan_action": "35",
    "prix_audit": "39",
    "prix_prestation": "250",
    "prix_bizplan": "250",
    "prix_serenite": "90",
    "prix_confort": "250",
    "prix_libere": "590",
    "calendly_url": "",
    "email_contact": "bonjour@bigdoc.fr",
    "telephone": "",
    "seuil_rdv": "50",
}


def get_app_settings() -> dict:
    """Récupère tous les paramètres de l'application."""
    conn = get_connection()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    result = dict(SETTINGS_DEFAULTS)
    for row in rows:
        result[row["key"]] = row["value"]
    # Convertir les numériques
    for k in ["prix_plan_action","prix_audit","prix_prestation","prix_bizplan",
              "prix_serenite","prix_confort","prix_libere","seuil_rdv"]:
        try: result[k] = int(result[k])
        except: pass
    return result


def save_app_settings(data: dict):
    """Sauvegarde les paramètres dans la base."""
    conn = get_connection()
    for key, value in data.items():
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value))
        )
    conn.commit()
    conn.close()


def get_stats() -> dict:
    """Stats anonymisées pour LinkedIn / SEO."""
    conn = get_connection()
    total = conn.execute("SELECT COUNT(*) as n FROM diagnostics WHERE score_global IS NOT NULL").fetchone()["n"]
    avg_score = conn.execute("SELECT AVG(score_global) as s FROM diagnostics WHERE score_global IS NOT NULL").fetchone()["s"]
    avg_hours = conn.execute("SELECT AVG(heures_perdues_semaine) as h FROM diagnostics WHERE heures_perdues_semaine > 0").fetchone()["h"]
    avg_euros = conn.execute("SELECT AVG(euros_evitables_an) as e FROM diagnostics WHERE euros_evitables_an > 0").fetchone()["e"]
    conn.close()
    return {
        "diagnostics_total": total,
        "score_moyen": round(avg_score or 0),
        "heures_perdues_moyennes": round(avg_hours or 0, 1),
        "euros_evitables_moyens": round(avg_euros or 0, -2),
    }

# ─────────────────────────────────────────
# CRM — FICHE LEAD
# ─────────────────────────────────────────

LEAD_STATUTS = ['lead', 'rdv', 'devis_envoye', 'devis_accepte', 'en_cours', 'livre', 'cloture', 'perdu']

def _migrate_lead_crm(conn):
    """Migration — ajoute colonnes CRM si manquantes."""
    for col, defval in [("rpps", "TEXT"), ("status", "TEXT DEFAULT 'lead'")]:
        try:
            conn.execute(f"ALTER TABLE leads ADD COLUMN {col} {defval}")
        except Exception:
            pass
    conn.execute("""
        CREATE TABLE IF NOT EXISTS lead_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER NOT NULL REFERENCES leads(id),
            type TEXT NOT NULL,
            content TEXT,
            meta TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()

def get_lead_fiche(lead_id: int) -> dict | None:
    """Retourne la fiche complète d'un lead — infos + diagnostics + events."""
    conn = get_connection()
    _migrate_lead_crm(conn)

    lead = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    if not lead:
        conn.close()
        return None
    lead = dict(lead)

    # Diagnostics
    diags = conn.execute("""
        SELECT session_id, score_global, niveau, recommandation_palier,
               heures_perdues_semaine, euros_evitables_an, created_at, phase
        FROM diagnostics WHERE lead_id=? ORDER BY created_at DESC
    """, (lead_id,)).fetchall()
    lead['diagnostics'] = [dict(d) for d in diags]

    # Events (notes + changements statut)
    events = conn.execute("""
        SELECT * FROM lead_events WHERE lead_id=? ORDER BY created_at DESC
    """, (lead_id,)).fetchall()
    lead['events'] = [dict(e) for e in events]

    conn.close()
    return lead

def update_lead_status(lead_id: int, status: str, note: str = "") -> bool:
    """Met à jour le statut d'un lead et log l'événement."""
    if status not in LEAD_STATUTS:
        return False
    conn = get_connection()
    _migrate_lead_crm(conn)
    conn.execute("UPDATE leads SET status=? WHERE id=?", (status, lead_id))
    conn.execute("""
        INSERT INTO lead_events (lead_id, type, content)
        VALUES (?, 'status', ?)
    """, (lead_id, f"Statut → {status}" + (f" — {note}" if note else "")))
    conn.commit()
    conn.close()
    return True

def add_lead_note(lead_id: int, content: str) -> int:
    """Ajoute une note libre sur un lead."""
    conn = get_connection()
    _migrate_lead_crm(conn)
    cur = conn.execute("""
        INSERT INTO lead_events (lead_id, type, content)
        VALUES (?, 'note', ?)
    """, (lead_id, content))
    event_id = cur.lastrowid
    conn.commit()
    conn.close()
    return event_id

def delete_lead_event(event_id: int) -> bool:
    """Supprime une note (pas les events système)."""
    conn = get_connection()
    _migrate_lead_crm(conn)
    conn.execute("DELETE FROM lead_events WHERE id=? AND type='note'", (event_id,))
    conn.commit()
    conn.close()
    return True

def update_lead_info(lead_id: int, data: dict) -> bool:
    """Met à jour les infos d'un lead (rpps, prenom, nom, etc.)."""
    allowed = ['prenom', 'nom', 'specialite', 'ville', 'rpps']
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        return False
    conn = get_connection()
    _migrate_lead_crm(conn)
    sets = ', '.join(f"{k}=?" for k in updates)
    conn.execute(f"UPDATE leads SET {sets} WHERE id=?", (*updates.values(), lead_id))
    conn.commit()
    conn.close()
    return True
