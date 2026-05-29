"""
Bigdoc — Utilitaire création mot de passe admin
Usage : python create_admin.py
"""
import hashlib
import secrets
import sqlite3
import os
import getpass
from dotenv import load_dotenv

load_dotenv()
DATABASE_PATH = os.getenv("DATABASE_PATH", "data/bigdoc.db")


def hash_password(password: str) -> str:
    """Hash le mot de passe avec SHA-256 + sel aléatoire."""
    salt = secrets.token_hex(32)
    hashed = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100_000)
    return f"{salt}${hashed.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Vérifie un mot de passe contre son hash stocké."""
    try:
        salt, hashed = stored.split('$')
        check = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100_000)
        return check.hex() == hashed
    except Exception:
        return False


def create_admin_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS admin_users (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            username  TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()


def main():
    print("\n  ██████╗ ██╗ ██████╗ ██████╗  ██████╗  ██████╗")
    print("  ██╔══██╗██║██╔════╝ ██╔══██╗██╔═══██╗██╔════╝")
    print("  ██████╔╝██║██║  ███╗██║  ██║██║   ██║██║     ")
    print("  ██╔══██╗██║██║   ██║██║  ██║██║   ██║██║     ")
    print("  ██████╔╝██║╚██████╔╝██████╔╝╚██████╔╝╚██████╗")
    print("  ╚═════╝ ╚═╝ ╚═════╝ ╚═════╝  ╚═════╝  ╚═════╝")
    print("\n  Création du compte administrateur")
    print("  ─────────────────────────────────\n")

    # Connexion base
    os.makedirs(os.path.dirname(DATABASE_PATH) if os.path.dirname(DATABASE_PATH) else '.', exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH)
    create_admin_table(conn)

    # Vérifier si un admin existe déjà
    existing = conn.execute("SELECT COUNT(*) FROM admin_users").fetchone()[0]
    if existing > 0:
        print(f"  ⚠️  {existing} compte(s) admin existent déjà.")
        choice = input("  Créer un compte supplémentaire ? (o/N) : ").strip().lower()
        if choice != 'o':
            print("\n  Annulé.\n")
            conn.close()
            return

    # Saisie
    username = input("  Nom d'utilisateur [admin] : ").strip() or "admin"

    while True:
        password = getpass.getpass("  Mot de passe (invisible) : ")
        if len(password) < 8:
            print("  ❌ Trop court — minimum 8 caractères.")
            continue
        confirm = getpass.getpass("  Confirmer le mot de passe : ")
        if password != confirm:
            print("  ❌ Les mots de passe ne correspondent pas.")
            continue
        break

    # Hashage et stockage
    password_hash = hash_password(password)

    try:
        conn.execute(
            "INSERT INTO admin_users (username, password_hash) VALUES (?, ?)",
            (username, password_hash)
        )
        conn.commit()
        print(f"\n  ✅ Compte '{username}' créé avec succès.")
        print("  Le mot de passe est stocké hashé — jamais en clair.")
        print("\n  Accédez au back office : http://localhost:8000/admin\n")
    except sqlite3.IntegrityError:
        print(f"\n  ❌ L'utilisateur '{username}' existe déjà.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
