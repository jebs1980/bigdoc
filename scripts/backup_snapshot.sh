#!/bin/bash
# Crée un snapshot horodaté de la branche dev sur GitHub
# Usage: ./backup_snapshot.sh [message]
# Requires: GITHUB_TOKEN env var ou /opt/bigdoc-dev/.env

REPO="jebs1980/bigdoc"
TOKEN="${GITHUB_TOKEN:-$(grep GITHUB_TOKEN /opt/bigdoc-dev/.env 2>/dev/null | cut -d'=' -f2)}"
if [ -z "$TOKEN" ]; then
  echo "ERREUR: GITHUB_TOKEN non défini"
  exit 1
fi

TIMESTAMP=$(date +"%Y%m%d_%H%M")
BRANCH_NAME="backup/dev_${TIMESTAMP}"
MESSAGE="${1:-Snapshot automatique}"

echo "[$(date)] Création snapshot: $BRANCH_NAME"

SHA=$(curl -s -H "Authorization: token $TOKEN" \
  "https://api.github.com/repos/$REPO/git/refs/heads/dev" | \
  python3 -c "import sys,json; print(json.load(sys.stdin)['object']['sha'])")

if [ -z "$SHA" ]; then
  echo "ERREUR: impossible de récupérer le SHA de dev"
  exit 1
fi

echo "  SHA dev: ${SHA:0:8}"

RESULT=$(curl -s -X POST \
  -H "Authorization: token $TOKEN" \
  -H "Content-Type: application/json" \
  "https://api.github.com/repos/$REPO/git/refs" \
  -d "{\"ref\": \"refs/heads/$BRANCH_NAME\", \"sha\": \"$SHA\"}")

if echo "$RESULT" | grep -q '"ref"'; then
  echo "  OK Snapshot créé: $BRANCH_NAME"
else
  echo "  ERREUR: $RESULT"
fi

# Nettoyer les snapshots > 24h
echo "[$(date)] Nettoyage des snapshots > 24h..."
BRANCHES=$(curl -s -H "Authorization: token $TOKEN" \
  "https://api.github.com/repos/$REPO/branches?per_page=100" | \
  python3 -c "
import sys,json,re
from datetime import datetime, timezone, timedelta
branches = json.load(sys.stdin)
cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
for b in branches:
    name = b['name']
    if name.startswith('backup/dev_'):
        m = re.search(r'(\d{8})_(\d{4})', name)
        if m:
            dt = datetime.strptime(m.group(1)+m.group(2), '%Y%m%d%H%M').replace(tzinfo=timezone.utc)
            if dt < cutoff:
                print(name)
")

for branch in $BRANCHES; do
  curl -s -X DELETE \
    -H "Authorization: token $TOKEN" \
    "https://api.github.com/repos/$REPO/git/refs/heads/$branch" > /dev/null
  echo "  Supprimé: $branch"
done

echo "[$(date)] Terminé."
