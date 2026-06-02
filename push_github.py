"""
Bigdoc — Helper de push GitHub
Toutes les modifications vont sur 'dev' par défaut.
Le token est lu depuis la variable d'environnement GITHUB_TOKEN
ou depuis le fichier .github_token (non commité)
"""
import urllib.request, json, base64, os

def get_token():
    # 1. Variable d'environnement
    t = os.getenv("GITHUB_TOKEN")
    if t: return t
    # 2. Fichier local non commité
    tf = os.path.join(os.path.dirname(__file__), ".github_token")
    if os.path.exists(tf):
        return open(tf).read().strip()
    raise ValueError("GITHUB_TOKEN non trouvé — créer .github_token ou définir la variable d'env")

REPO = "jebs1980/bigdoc"
BASE = "https://api.github.com"
DEFAULT_BRANCH = "dev"

def get_headers():
    return {
        "Authorization": f"token {get_token()}",
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json"
    }

def api(method, path, data=None):
    req = urllib.request.Request(f"{BASE}{path}", headers=get_headers(), method=method)
    if data: req.data = json.dumps(data).encode()
    try:
        with urllib.request.urlopen(req) as r: return json.loads(r.read()), r.status
    except urllib.error.HTTPError as e: return json.loads(e.read()), e.code

def get_sha(repo_path, branch=DEFAULT_BRANCH):
    resp, status = api("GET", f"/repos/{REPO}/contents/{repo_path}?ref={branch}")
    return resp.get("sha") if status == 200 else None

def push_file(filepath, repo_path, msg, branch=DEFAULT_BRANCH):
    with open(filepath, "rb") as f:
        content = base64.b64encode(f.read()).decode()
    sha = get_sha(repo_path, branch)
    data = {"message": msg, "content": content, "branch": branch}
    if sha: data["sha"] = sha
    _, status = api("PUT", f"/repos/{REPO}/contents/{repo_path}", data)
    icon = "✅" if status in (200, 201) else "❌"
    print(f"{icon} [{branch}] {repo_path} — {status}")

def push_files(file_list, branch=DEFAULT_BRANCH):
    for local, remote, msg in file_list:
        if os.path.exists(local):
            push_file(local, remote, msg, branch)
        else:
            print(f"⚠️  {local} non trouvé")

def merge_dev_to_main():
    resp, status = api("POST", f"/repos/{REPO}/merges", {
        "base": "main",
        "head": "dev",
        "commit_message": "Merge dev → main (production)"
    })
    if status in (200, 201):
        print("✅ dev → main mergé")
    elif status == 204:
        print("ℹ️  Déjà à jour")
    else:
        print(f"❌ Merge échoué : {status} — {resp.get('message', '')}")

if __name__ == "__main__":
    print("Branches disponibles :")
    resp, _ = api("GET", f"/repos/{REPO}/branches")
    for b in resp:
        print(f"  → {b['name']}")
