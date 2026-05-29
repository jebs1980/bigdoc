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
        CREATE TABLE IF NOT EXISTS leads (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            email           TEXT NOT NULL,
            prenom          TEXT,
            specialite      TEXT,
            ville           TEXT,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            rgpd_consent    INTEGER DEFAULT 1,
            rgpd_date       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            source          TEXT DEFAULT 'diagnostic'
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
    reco = bilan.get("recommandation_principale", {})

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
        dims.get("administration", {}).get("score"),
        dims.get("achats_materiel", {}).get("score"),
        dims.get("informatique", {}).get("score"),
        dims.get("comptabilite", {}).get("score"),
        dims.get("charge_mentale", {}).get("score"),
        dims.get("financement", {}).get("score"),
        dims.get("developpement", {}).get("score"),
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


def save_lead(session_id: str, email: str, prenom: str = "", specialite: str = "", ville: str = "") -> int:
    conn = get_connection()

    # Upsert lead
    conn.execute("""
        INSERT INTO leads (email, prenom, specialite, ville)
        VALUES (?, ?, ?, ?)
        ON CONFLICT DO NOTHING
    """, (email.lower().strip(), prenom, specialite, ville))

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
    rows = conn.execute("""
        SELECT
            d.session_id,
            d.created_at as date,
            l.email,
            l.prenom,
            l.specialite,
            l.ville,
            d.phase,
            d.score_global,
            d.dim_administration,
            d.dim_achats_materiel,
            d.dim_informatique,
            d.dim_comptabilite,
            d.dim_charge_mentale,
            d.dim_financement,
            d.dim_developpement,
            d.heures_perdues_semaine,
            d.euros_evitables_an,
            d.recommandation_palier,
            d.recommandation_tarif
        FROM diagnostics d
        LEFT JOIN leads l ON l.id = d.lead_id
        WHERE d.score_global IS NOT NULL
        ORDER BY d.created_at DESC
        LIMIT 500
    """).fetchall()
    conn.close()
    return [dict(row) for row in rows]
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
