#!/bin/bash
# ─────────────────────────────────────────────────────────────
# Bigdoc — Setup cron jobs sur VPS Linux
# À lancer une seule fois après installation sur le VPS
# ─────────────────────────────────────────────────────────────

BIGDOC_DIR="/opt/bigdoc"
PYTHON="$BIGDOC_DIR/.venv/bin/python"
LOG_DIR="/var/log/bigdoc"

# Créer le répertoire de logs
mkdir -p $LOG_DIR

echo "Configuration des crons Bigdoc..."

# Supprimer les anciens crons Bigdoc
crontab -l 2>/dev/null | grep -v "bigdoc" > /tmp/crontab_clean

cat >> /tmp/crontab_clean << 'CRON'
# ── BIGDOC CRONS ──────────────────────────────────────────────

# Healthcheck quotidien à 7h — alerte email si modèle déprécié
0 7 * * * curl -s https://bigdoc.fr/api/health >> /var/log/bigdoc/health.log 2>&1

# Mise à jour démographie médicale — 1er de chaque mois à 3h
0 3 1 * * cd /opt/bigdoc && /opt/bigdoc/.venv/bin/python scripts/update_demographics.py >> /var/log/bigdoc/demographics.log 2>&1 && docker compose restart

# Backup base SQLite — tous les jours à 2h
0 2 * * * cp /opt/bigdoc/data/bigdoc.db /opt/bigdoc/data/backups/bigdoc_$(date +\%Y\%m\%d).db 2>/dev/null; find /opt/bigdoc/data/backups -name "*.db" -mtime +30 -delete

# Nettoyage logs — tous les dimanches à 4h
0 4 * * 0 find /var/log/bigdoc -name "*.log" -size +50M -exec truncate -s 0 {} \;

# ──────────────────────────────────────────────────────────────
CRON

crontab /tmp/crontab_clean
rm /tmp/crontab_clean

echo "✅ Crons configurés :"
crontab -l | grep -A 20 "BIGDOC CRONS"

# Créer le répertoire de backups
mkdir -p $BIGDOC_DIR/data/backups

echo ""
echo "✅ Setup terminé"
echo "   Logs : $LOG_DIR/"
echo "   Backup SQLite : $BIGDOC_DIR/data/backups/"
