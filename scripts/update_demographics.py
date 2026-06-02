"""
Bigdoc — Mise à jour des données démographiques médicales
Source : données RPPS / data.gouv.fr + DREES

Usage :
    python scripts/update_demographics.py

Ce script :
1. Télécharge le fichier RPPS depuis data.gouv.fr
2. Calcule les densités par département et spécialité
3. Met à jour data/demographics.json

À lancer localement (pas depuis le serveur) — accès internet requis.
Fréquence recommandée : 1 fois par mois.
"""

import json
import os
import sys
import datetime
import urllib.request

# ── CONFIG ──
DEMOGRAPHICS_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'demographics.json')

# URL du fichier RPPS sur data.gouv.fr
# Vérifier l'URL actuelle sur : https://www.data.gouv.fr/fr/datasets/annuaire-sante-professionnels-de-sante/
RPPS_URL = "https://www.data.gouv.fr/fr/datasets/r/a88a0b18-57c4-4e03-8f82-0d2ede81b9d8"

# Population par département (INSEE 2023)
POPULATION_DEPT = {
    "01": 660000, "02": 534000, "03": 335000, "04": 165000, "05": 142000,
    "06": 1094000, "07": 333000, "08": 274000, "09": 154000, "10": 310000,
    "11": 378000, "12": 279000, "13": 2066000, "14": 700000, "15": 144000,
    "16": 355000, "17": 649000, "18": 301000, "19": 239000, "21": 536000,
    "22": 603000, "23": 113000, "24": 415000, "25": 545000, "26": 514000,
    "27": 601000, "28": 431000, "29": 912000, "30": 747000, "31": 1400000,
    "32": 197000, "33": 1600000, "34": 1155000, "35": 1060000, "36": 224000,
    "37": 606000, "38": 1272000, "39": 263000, "40": 420000, "41": 334000,
    "42": 764000, "43": 225000, "44": 1430000, "45": 672000, "46": 173000,
    "47": 336000, "48": 76000, "49": 800000, "50": 492000, "51": 570000,
    "52": 174000, "53": 307000, "54": 733000, "55": 190000, "56": 750000,
    "57": 1040000, "58": 213000, "59": 2620000, "60": 825000, "61": 285000,
    "62": 1490000, "63": 660000, "64": 680000, "65": 229000, "66": 479000,
    "67": 1125000, "68": 775000, "69": 1875000, "70": 238000, "71": 557000,
    "72": 566000, "73": 430000, "74": 830000, "75": 2175000, "76": 1260000,
    "77": 1430000, "78": 1440000, "79": 374000, "80": 571000, "81": 390000,
    "82": 260000, "83": 1080000, "84": 565000, "85": 690000, "86": 440000,
    "87": 376000, "88": 370000, "89": 334000, "90": 163000, "91": 1300000,
    "92": 1630000, "93": 1650000, "94": 1380000, "95": 1220000,
}

# Mapping spécialités RPPS → spécialités Bigdoc
SPECIALITE_MAP = {
    "Médecine générale": "Médecine générale",
    "Gynécologie-Obstétrique": "Gynécologie-obstétrique",
    "Gynécologie médicale": "Gynécologie médicale",
    "Cardiologie et Maladies Vasculaires": "Cardiologie",
    "Pédiatrie": "Pédiatrie",
    "Psychiatrie": "Psychiatrie",
    "Dermatologie et Vénéréologie": "Dermatologie",
    "Ophtalmologie": "Ophtalmologie",
    "Chirurgie Orthopédique et Traumatologie": "Orthopédie",
    "Gastro-entérologie et Hépatologie": "Gastro-entérologie",
    "Pneumologie": "Pneumologie",
    "Neurologie": "Neurologie",
    "Rhumatologie": "Rhumatologie",
    "Endocrinologie, Diabétologie et Maladies Métaboliques": "Endocrinologie",
    "Urologie": "Urologie",
    "Oto-Rhino-Laryngologie": "ORL",
    "Radiodiagnostic et Imagerie Médicale": "Radiologie",
    "Anesthésiologie-Réanimation": "Anesthésie-réanimation",
    "Chirurgie Générale": "Chirurgie générale",
    "Médecine Interne": "Médecine interne",
}


def download_rpps():
    """Télécharge le fichier RPPS depuis data.gouv.fr"""
    print("📥 Téléchargement du fichier RPPS...")
    print(f"   Source : {RPPS_URL}")
    print("   Ce fichier peut être volumineux (>100 Mo) — patientez...")

    try:
        req = urllib.request.Request(RPPS_URL, headers={'User-Agent': 'Bigdoc/1.0'})
        with urllib.request.urlopen(req, timeout=120) as response:
            content = response.read().decode('utf-8', errors='ignore')
        print(f"   ✅ Téléchargé ({len(content)//1024//1024} Mo)")
        return content
    except Exception as e:
        print(f"   ❌ Erreur : {e}")
        print("\n   💡 Téléchargez manuellement depuis :")
        print("   https://www.data.gouv.fr/fr/datasets/annuaire-sante-professionnels-de-sante/")
        print("   Puis relancez avec : python scripts/update_demographics.py --file chemin/vers/fichier.csv")
        return None


def process_rpps(content):
    """Traite le CSV RPPS et calcule les densités par département."""
    print("\n🔄 Traitement des données...")

    counts = {}  # {dept: {specialite: count}}
    lines = content.split('\n')
    header = None
    processed = 0
    errors = 0

    for i, line in enumerate(lines):
        if i == 0:
            header = [col.strip('"').strip() for col in line.split('|')]
            print(f"   Colonnes trouvées : {header[:8]}")
            continue

        parts = line.split('|')
        if len(parts) < 5:
            continue

        try:
            # Les colonnes varient selon la version du fichier
            # Adapter selon le header réel
            spe_col = None
            dept_col = None
            mode_col = None

            for j, col in enumerate(header):
                col_lower = col.lower()
                if 'specialit' in col_lower and spe_col is None:
                    spe_col = j
                if 'departement' in col_lower and dept_col is None:
                    dept_col = j
                if 'mode' in col_lower and 'exercice' in col_lower and mode_col is None:
                    mode_col = j

            if spe_col is None or dept_col is None:
                if errors == 0:
                    print(f"   ⚠️  Colonnes non trouvées. Header : {header}")
                errors += 1
                continue

            specialite = parts[spe_col].strip('"').strip() if spe_col < len(parts) else ''
            dept = parts[dept_col].strip('"').strip()[:2] if dept_col < len(parts) else ''
            mode = parts[mode_col].strip('"').strip() if mode_col and mode_col < len(parts) else 'Libéral'

            # Garder uniquement les libéraux
            if mode and 'libéral' not in mode.lower() and 'mixte' not in mode.lower():
                continue

            # Normaliser le département
            if len(dept) == 1:
                dept = '0' + dept

            if not dept or dept not in POPULATION_DEPT:
                continue

            # Mapper la spécialité
            spe_bigdoc = SPECIALITE_MAP.get(specialite, None)
            if not spe_bigdoc:
                continue

            if dept not in counts:
                counts[dept] = {}
            counts[dept][spe_bigdoc] = counts[dept].get(spe_bigdoc, 0) + 1
            processed += 1

        except Exception:
            errors += 1

    print(f"   ✅ {processed} médecins libéraux traités ({errors} erreurs ignorées)")
    return counts


def calculate_densities(counts):
    """Calcule les densités pour 100 000 habitants."""
    densities = {}
    for dept, specialites in counts.items():
        pop = POPULATION_DEPT.get(dept, 100000)
        densities[dept] = {}
        for spe, count in specialites.items():
            densities[dept][spe] = round((count / pop) * 100000, 1)
    return densities


def update_demographics(densities=None):
    """Met à jour le fichier demographics.json avec les nouvelles densités."""

    # Charger le fichier existant
    with open(DEMOGRAPHICS_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)

    if densities:
        # Mettre à jour les densités par département
        updated = 0
        for dept, dept_data in data.get('departements', {}).items():
            if dept in densities:
                dept_data['densites'] = densities[dept]
                updated += 1

        # Calculer les densités nationales
        for spe in data.get('densites_nationales', {}):
            all_counts = []
            for dept, dept_densities in densities.items():
                if spe in dept_densities:
                    all_counts.append(dept_densities[spe])
            if all_counts:
                data['densites_nationales'][spe] = round(sum(all_counts) / len(all_counts), 1)

        print(f"\n✅ {updated} départements mis à jour")
    else:
        print("\n⚠️  Pas de nouvelles densités — seules les métadonnées sont mises à jour")

    # Mettre à jour la date
    data['_meta']['updated'] = datetime.date.today().strftime('%Y-%m')
    data['_meta']['source'] = f"RPPS ANS / DREES — Mis à jour le {datetime.date.today().strftime('%d/%m/%Y')}"

    with open(DEMOGRAPHICS_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"✅ Fichier mis à jour : {DEMOGRAPHICS_PATH}")


def main():
    print("\n  BIGDOC — Mise à jour démographie médicale")
    print("  ─────────────────────────────────────────\n")

    # Mode avec fichier local
    if '--file' in sys.argv:
        idx = sys.argv.index('--file')
        if idx + 1 < len(sys.argv):
            filepath = sys.argv[idx + 1]
            print(f"📂 Lecture du fichier local : {filepath}")
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
        else:
            print("❌ Indiquer le chemin du fichier : --file chemin/vers/rpps.csv")
            sys.exit(1)
    else:
        content = download_rpps()
        if not content:
            # Mise à jour partielle (métadonnées seulement)
            update_demographics()
            sys.exit(0)

    counts = process_rpps(content)
    if counts:
        densities = calculate_densities(counts)
        update_demographics(densities)
    else:
        print("❌ Aucune donnée extraite — vérifiez le format du fichier")
        sys.exit(1)


if __name__ == '__main__':
    main()
