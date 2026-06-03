"""
Bigdoc — Script d'évaluation batch
Lance les cas de test contre le diagnostic.

Usage :
  python3 run_eval.py                      # mode normal, tous les 50 cas
  python3 run_eval.py --batch              # mode batch API (50% moins cher, ~2h)
  python3 run_eval.py --batch-status ID   # vérifier statut d'un batch
  python3 run_eval.py --batch-get ID      # récupérer résultats d'un batch terminé
  python3 run_eval.py TC001 TC002         # cas spécifiques en mode normal
  python3 run_eval.py --depuis TC010      # reprendre depuis un ID
"""
import json, sys, time, os, re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
os.chdir(Path(__file__).parent)

from config import SYSTEM_PROMPT, ANTHROPIC_API_KEY, DATABASE_PATH
import anthropic

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

CASES_FILE   = Path("eval_cases.json")
RESULTS_FILE = Path("eval_results.json")
BATCHES_FILE = Path("eval_batches.json")  # historique des batches lancés


# ─── HELPERS ─────────────────────────────────────────────────────────────────

def load_cases(args):
    """Charge et filtre les cas selon les arguments CLI."""
    cases = json.loads(CASES_FILE.read_text())
    if "--depuis" in args:
        idx = args.index("--depuis")
        depuis_id = args[idx + 1] if idx + 1 < len(args) else None
        if depuis_id:
            ids = [c["id"] for c in cases]
            if depuis_id in ids:
                cases = cases[ids.index(depuis_id):]
    else:
        ids_filtres = {a for a in args if a.startswith("TC")}
        if ids_filtres:
            cases = [c for c in cases if c["id"] in ids_filtres]
    return cases


def build_prompt(case):
    """Construit le prompt utilisateur pour un cas."""
    from main import build_diagnostic_prompt, get_demographic_context
    context = get_demographic_context(case["specialite"], case["ville"])
    user_prompt = build_diagnostic_prompt(
        reponses=case["reponses"],
        texte_libre=case.get("texte_libre", ""),
        specialite=case["specialite"],
        ville=case["ville"],
        catalogue="",
    )
    if context:
        user_prompt = context + "\n\n" + user_prompt
    return user_prompt


def parse_bilan(raw):
    """Parse le JSON du bilan avec fallback."""
    try:
        return json.loads(raw.strip())
    except json.JSONDecodeError:
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except Exception:
                pass
    return {"erreur": "JSON invalide", "raw": raw[:300]}


def save_results(results):
    RESULTS_FILE.write_text(json.dumps(results, ensure_ascii=False, indent=2))


def load_results():
    if RESULTS_FILE.exists():
        return {r["id"]: r for r in json.loads(RESULTS_FILE.read_text())}
    return {}


def save_batch_record(batch_id, case_ids):
    records = []
    if BATCHES_FILE.exists():
        records = json.loads(BATCHES_FILE.read_text())
    records.append({
        "batch_id": batch_id,
        "case_ids": case_ids,
        "launched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "status": "in_progress"
    })
    BATCHES_FILE.write_text(json.dumps(records, ensure_ascii=False, indent=2))


# ─── MODE NORMAL ─────────────────────────────────────────────────────────────

def run_normal(cases):
    print(f"\n🔬 Mode normal — {len(cases)} cas\n")
    results = load_results()

    for i, case in enumerate(cases, 1):
        print(f"[{i}/{len(cases)}] {case['id']} : {case['label']}...", end=" ", flush=True)
        try:
            user_prompt = build_prompt(case)
            response = client.messages.create(
                model="claude-sonnet-4-5",
                max_tokens=4000,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}]
            )
            bilan = parse_bilan(response.content[0].text)
            status = "ok" if "score_global" in bilan else "erreur"
            print(f"✅ Score {bilan.get('score_global','?')}/100 — {str(bilan.get('titre_personnalise', bilan.get('niveau','?')))[:50]}")
        except Exception as e:
            bilan, status = {}, "erreur"
            print(f"❌ {e}")

        results[case["id"]] = {
            "id": case["id"],
            "label": case["label"],
            "specialite": case["specialite"],
            "ville": case["ville"],
            "texte_libre": case.get("texte_libre", ""),
            "bilan": bilan,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "status": status
        }
        save_results(list(results.values()))
        if i < len(cases):
            time.sleep(1.5)

    ok = sum(1 for r in results.values() if r.get("status") == "ok")
    print(f"\n✅ Terminé — {ok}/{len(results)} cas OK")
    print(f"📁 {RESULTS_FILE.absolute()}")


# ─── MODE BATCH API ──────────────────────────────────────────────────────────

def run_batch(cases):
    print(f"\n🚀 Mode Batch API — {len(cases)} cas (50% moins cher, résultats dans ~2h)\n")

    requests = []
    for case in cases:
        user_prompt = build_prompt(case)
        requests.append({
            "custom_id": case["id"],
            "params": {
                "model": "claude-sonnet-4-5",
                "max_tokens": 4000,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user_prompt}]
            }
        })

    batch = client.beta.messages.batches.create(requests=requests)
    batch_id = batch.id
    case_ids = [c["id"] for c in cases]

    save_batch_record(batch_id, case_ids)

    print(f"✅ Batch lancé : {batch_id}")
    print(f"   {len(requests)} requêtes envoyées")
    print(f"\nPour vérifier le statut :")
    print(f"  python3 run_eval.py --batch-status {batch_id}")
    print(f"\nPour récupérer les résultats quand c'est terminé :")
    print(f"  python3 run_eval.py --batch-get {batch_id}")


def batch_status(batch_id):
    batch = client.beta.messages.batches.retrieve(batch_id)
    counts = batch.request_counts
    print(f"\n📊 Batch {batch_id}")
    print(f"   Statut    : {batch.processing_status}")
    print(f"   Succès    : {counts.succeeded}")
    print(f"   Erreurs   : {counts.errored}")
    print(f"   En cours  : {counts.processing}")
    print(f"   Annulés   : {counts.canceled}")
    if batch.processing_status == "ended":
        print(f"\n✅ Terminé — lance : python3 run_eval.py --batch-get {batch_id}")
    else:
        print(f"\n⏳ Toujours en cours — reviens dans quelques minutes")


def batch_get(batch_id):
    batch = client.beta.messages.batches.retrieve(batch_id)
    if batch.processing_status != "ended":
        print(f"⏳ Pas encore terminé (statut : {batch.processing_status})")
        return

    print(f"\n📥 Récupération des résultats du batch {batch_id}...\n")
    results = load_results()
    ok, errors = 0, 0

    # Charger les cas pour avoir les métadonnées
    cases_by_id = {c["id"]: c for c in json.loads(CASES_FILE.read_text())}

    for result in client.beta.messages.batches.results(batch_id):
        cid = result.custom_id
        case = cases_by_id.get(cid, {})
        if result.result.type == "succeeded":
            raw = result.result.message.content[0].text
            bilan = parse_bilan(raw)
            status = "ok" if "score_global" in bilan else "erreur"
            ok += 1
            print(f"  ✅ {cid} — Score {bilan.get('score_global','?')}/100")
        else:
            bilan, status = {}, "erreur"
            errors += 1
            print(f"  ❌ {cid} — {result.result.type}")

        results[cid] = {
            "id": cid,
            "label": case.get("label", ""),
            "specialite": case.get("specialite", ""),
            "ville": case.get("ville", ""),
            "texte_libre": case.get("texte_libre", ""),
            "bilan": bilan,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "status": status,
            "mode": "batch"
        }

    save_results(list(results.values()))
    print(f"\n✅ {ok} OK / {errors} erreurs")
    print(f"📁 {RESULTS_FILE.absolute()}")


# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]

    if "--batch-status" in args:
        idx = args.index("--batch-status")
        bid = args[idx + 1] if idx + 1 < len(args) else None
        if bid:
            batch_status(bid)
        else:
            print("Usage : python3 run_eval.py --batch-status BATCH_ID")
        return

    if "--batch-get" in args:
        idx = args.index("--batch-get")
        bid = args[idx + 1] if idx + 1 < len(args) else None
        if bid:
            batch_get(bid)
        else:
            print("Usage : python3 run_eval.py --batch-get BATCH_ID")
        return

    cases = load_cases(args)
    if not cases:
        print("❌ Aucun cas trouvé")
        return

    if "--batch" in args:
        run_batch(cases)
    else:
        run_normal(cases)


if __name__ == "__main__":
    main()
