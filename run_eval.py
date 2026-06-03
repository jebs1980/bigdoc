"""
Bigdoc — Script d'évaluation batch
Lance les 50 cas de test contre le diagnostic et stocke les résultats.

Usage :
  python3 run_eval.py                    # tous les cas
  python3 run_eval.py TC001 TC002        # cas spécifiques
  python3 run_eval.py --depuis TC010     # depuis un ID donné
"""
import json, sys, time, os, sqlite3, asyncio
from pathlib import Path

# Ajouter le répertoire courant au path
sys.path.insert(0, str(Path(__file__).parent))
os.chdir(Path(__file__).parent)

from config import SYSTEM_PROMPT, ANTHROPIC_API_KEY, DATABASE_PATH
from main import build_diagnostic_prompt, get_demographic_context

import anthropic

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

CASES_FILE = Path("eval_cases.json")
RESULTS_FILE = Path("eval_results.json")

def run_case(case: dict) -> dict:
    """Lance un cas de test et retourne le résultat."""
    print(f"  → {case['id']} : {case['label']}...", end=" ", flush=True)

    try:
        # Construire le contexte géographique
        context = get_demographic_context(case["specialite"], case["ville"])

        # Construire le prompt
        user_prompt = build_diagnostic_prompt(
            reponses=case["reponses"],
            texte_libre=case.get("texte_libre", ""),
            specialite=case["specialite"],
            ville=case["ville"],
            catalogue="",
        )
        if context:
            user_prompt = context + "\n\n" + user_prompt

        # Appel API
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=4000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}]
        )

        raw = response.content[0].text.strip()

        # Parser le JSON
        try:
            bilan = json.loads(raw)
        except json.JSONDecodeError:
            import re
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            bilan = json.loads(match.group()) if match else {"erreur": "JSON invalide", "raw": raw[:500]}

        result = {
            "id": case["id"],
            "label": case["label"],
            "specialite": case["specialite"],
            "ville": case["ville"],
            "texte_libre": case.get("texte_libre", ""),
            "bilan": bilan,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "status": "ok" if "score_global" in bilan else "erreur"
        }
        print(f"✅ Score {bilan.get('score_global','?')}/100 — {bilan.get('titre_personnalise', bilan.get('niveau','?'))[:50]}")
        return result

    except Exception as e:
        print(f"❌ {e}")
        return {
            "id": case["id"],
            "label": case["label"],
            "specialite": case["specialite"],
            "ville": case["ville"],
            "bilan": {},
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "status": "erreur",
            "erreur": str(e)
        }

def main():
    cases = json.loads(CASES_FILE.read_text())

    # Filtrer selon les arguments
    args = sys.argv[1:]
    if args and args[0] == "--depuis":
        depuis_id = args[1] if len(args) > 1 else None
        if depuis_id:
            ids = [c["id"] for c in cases]
            if depuis_id in ids:
                start = ids.index(depuis_id)
                cases = cases[start:]
    elif args:
        ids_filtres = set(args)
        cases = [c for c in cases if c["id"] in ids_filtres]

    # Charger les résultats existants
    existing = {}
    if RESULTS_FILE.exists():
        for r in json.loads(RESULTS_FILE.read_text()):
            existing[r["id"]] = r

    print(f"\n🔬 Bigdoc Eval — {len(cases)} cas à traiter\n")

    results = list(existing.values())
    ids_done = {r["id"] for r in results}

    for i, case in enumerate(cases, 1):
        print(f"[{i}/{len(cases)}] ", end="")
        result = run_case(case)
        # Remplacer ou ajouter
        results = [r for r in results if r["id"] != result["id"]]
        results.append(result)
        # Sauvegarder à chaque cas
        RESULTS_FILE.write_text(json.dumps(results, ensure_ascii=False, indent=2))
        # Pause pour éviter le rate limiting
        if i < len(cases):
            time.sleep(1.5)

    ok = sum(1 for r in results if r.get("status") == "ok")
    print(f"\n✅ Terminé — {ok}/{len(results)} cas OK")
    print(f"📁 Résultats : {RESULTS_FILE.absolute()}")

if __name__ == "__main__":
    main()
