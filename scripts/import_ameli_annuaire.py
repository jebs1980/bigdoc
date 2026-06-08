"""
Import du CSV Annuaire Santé Ameli dans SQLite
Usage : python3 import_ameli_annuaire.py
Source : https://www.data.gouv.fr/api/1/datasets/r/432983b9-2e6f-473a-b35a-20403c300a5f
Mise à jour : mensuelle (cron recommandé)
"""
import sqlite3, csv, urllib.request, os, sys, io, datetime, gzip

CSV_URL   = "https://www.data.gouv.fr/api/1/datasets/r/432983b9-2e6f-473a-b35a-20403c300a5f"
DB_PATH   = os.environ.get("DATABASE_PATH", "/data/bigdoc.db")
META_PATH = "/data/ameli_annuaire_meta.txt"

print(f"[{datetime.datetime.now():%H:%M:%S}] Téléchargement du CSV Ameli (~150 Mo)...")

try:
    req = urllib.request.Request(CSV_URL, headers={
        "User-Agent": "Bigdoc/1.0 (contact: admin@bigdoc.fr)"
    })
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = r.read()
    print(f"  → {len(raw)/1024/1024:.1f} Mo téléchargés")
except Exception as e:
    print(f"ERREUR téléchargement : {e}")
    sys.exit(1)

# Détecter l'encodage et parser le CSV
try:
    text = raw.decode("utf-8")
except UnicodeDecodeError:
    text = raw.decode("latin-1")

reader = csv.DictReader(io.StringIO(text), delimiter=";")
headers = reader.fieldnames or []
print(f"  → Colonnes détectées : {', '.join(headers[:15])}...")

# Trouver les colonnes RPPS, adresse, ville
def find_col(headers, candidates):
    for c in candidates:
        for h in headers:
            if c.lower() in h.lower():
                return h
    return None

col_rpps    = find_col(headers, ["rpps", "identifiant_rpps", "num_rpps"])
col_adresse = find_col(headers, ["adresse", "address", "voie"])
col_ville   = find_col(headers, ["ville", "commune", "city"])
col_cp      = find_col(headers, ["code_postal", "cp", "postal"])
col_secteur = find_col(headers, ["secteur", "convention", "conventionnel"])
col_spe     = find_col(headers, ["specialite", "profession", "libelle_profession"])

print(f"  → RPPS={col_rpps} | Adresse={col_adresse} | Ville={col_ville} | CP={col_cp} | Secteur={col_secteur} | Spé={col_spe}")

if not col_rpps:
    print("ERREUR : colonne RPPS introuvable. Colonnes disponibles :")
    print(", ".join(headers))
    sys.exit(1)

# Import SQLite
print(f"[{datetime.datetime.now():%H:%M:%S}] Import dans SQLite ({DB_PATH})...")
conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

cur.execute("DROP TABLE IF EXISTS ameli_annuaire")
cur.execute("""
    CREATE TABLE ameli_annuaire (
        rpps TEXT PRIMARY KEY,
        adresse TEXT,
        ville TEXT,
        code_postal TEXT,
        secteur TEXT,
        specialite TEXT,
        updated_at TEXT
    )
""")
cur.execute("CREATE INDEX IF NOT EXISTS idx_ameli_rpps ON ameli_annuaire(rpps)")

now = datetime.datetime.now().isoformat()
batch = []
count = 0
skipped = 0

for row in reader:
    rpps = (row.get(col_rpps) or "").strip()
    if not rpps or not rpps.isdigit():
        skipped += 1
        continue
    batch.append((
        rpps,
        (row.get(col_adresse) or "").strip() if col_adresse else "",
        (row.get(col_ville) or "").strip() if col_ville else "",
        (row.get(col_cp) or "").strip() if col_cp else "",
        (row.get(col_secteur) or "").strip() if col_secteur else "",
        (row.get(col_spe) or "").strip() if col_spe else "",
        now,
    ))
    count += 1
    if len(batch) >= 5000:
        cur.executemany("INSERT OR REPLACE INTO ameli_annuaire VALUES (?,?,?,?,?,?,?)", batch)
        batch = []
        if count % 50000 == 0:
            print(f"  → {count:,} lignes importées...")

if batch:
    cur.executemany("INSERT OR REPLACE INTO ameli_annuaire VALUES (?,?,?,?,?,?,?)", batch)

conn.commit()
conn.close()

# Sauvegarder la date d'import
with open(META_PATH, "w") as f:
    f.write(datetime.datetime.now().strftime("%d/%m/%Y"))

print(f"[{datetime.datetime.now():%H:%M:%S}] ✅ Import terminé — {count:,} médecins ({skipped:,} ignorés)")
print(f"  → Date sauvegardée dans {META_PATH}")
