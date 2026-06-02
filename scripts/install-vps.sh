#!/bin/bash
# ═══════════════════════════════════════════════════════
# VPS Setup — Real Med Services / Bigdoc
# Ubuntu 24 · WordPress + Bigdoc (Docker) + Nginx + SSL
#
# Usage : bash install-vps.sh
# ═══════════════════════════════════════════════════════

set -e
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}"
echo "  ██████╗ ██╗ ██████╗ ██████╗  ██████╗  ██████╗"
echo "  ██╔══██╗██║██╔════╝ ██╔══██╗██╔═══██╗██╔════╝"
echo "  ██████╔╝██║██║  ███╗██║  ██║██║   ██║██║     "
echo "  ██╔══██╗██║██║   ██║██║  ██║██║   ██║██║     "
echo "  ██████╔╝██║╚██████╔╝██████╔╝╚██████╔╝╚██████╗"
echo "  ╚═════╝ ╚═╝ ╚═════╝ ╚═════╝  ╚═════╝  ╚═════╝"
echo -e "${NC}"
echo "  Installation VPS — Real Med Services"
echo "  ─────────────────────────────────────"
echo ""

# ── CONFIG (à modifier avant lancement) ──
DOMAIN_RMS="realmedservices.com"
DOMAIN_BIGDOC="bigdoc.fr"
DB_NAME="wordpress_rms"
DB_USER="rms_user"
DB_PASS=$(openssl rand -base64 24)
DB_ROOT_PASS=$(openssl rand -base64 24)
WP_ADMIN_USER="admin"
WP_ADMIN_PASS=$(openssl rand -base64 16)
WP_ADMIN_EMAIL="bonjour@bigdoc.fr"
BIGDOC_DIR="/opt/bigdoc"

echo -e "${YELLOW}Domaines configurés :${NC}"
echo "  WordPress  → $DOMAIN_RMS"
echo "  Bigdoc     → $DOMAIN_BIGDOC"
echo ""
read -p "Continuer ? (oui/N) " confirm
[[ "$confirm" != "oui" ]] && exit 0

# ── MISE À JOUR SYSTÈME ──
echo -e "\n${GREEN}[1/8] Mise à jour système...${NC}"
apt-get update -qq && apt-get upgrade -y -qq

# ── NGINX ──
echo -e "\n${GREEN}[2/8] Installation Nginx...${NC}"
apt-get install -y -qq nginx
systemctl enable nginx

# ── PHP ──
echo -e "\n${GREEN}[3/8] Installation PHP 8.2...${NC}"
apt-get install -y -qq php8.2-fpm php8.2-mysql php8.2-xml \
  php8.2-gd php8.2-curl php8.2-mbstring php8.2-zip \
  php8.2-intl php8.2-bcmath php8.2-imagick
systemctl enable php8.2-fpm

# ── MARIADB ──
echo -e "\n${GREEN}[4/8] Installation MariaDB...${NC}"
apt-get install -y -qq mariadb-server
systemctl enable mariadb

mysql -e "CREATE DATABASE IF NOT EXISTS ${DB_NAME} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
mysql -e "CREATE USER IF NOT EXISTS '${DB_USER}'@'localhost' IDENTIFIED BY '${DB_PASS}';"
mysql -e "GRANT ALL PRIVILEGES ON ${DB_NAME}.* TO '${DB_USER}'@'localhost';"
mysql -e "FLUSH PRIVILEGES;"
echo -e "${GREEN}  ✅ Base de données créée${NC}"

# ── WORDPRESS ──
echo -e "\n${GREEN}[5/8] Installation WordPress...${NC}"
mkdir -p /var/www/${DOMAIN_RMS}
cd /var/www/${DOMAIN_RMS}
wget -q https://fr.wordpress.org/latest-fr_FR.tar.gz
tar -xzf latest-fr_FR.tar.gz --strip-components=1
rm latest-fr_FR.tar.gz

# wp-config.php
cp wp-config-sample.php wp-config.php
sed -i "s/database_name_here/${DB_NAME}/" wp-config.php
sed -i "s/username_here/${DB_USER}/" wp-config.php
sed -i "s/password_here/${DB_PASS}/" wp-config.php

# Salts de sécurité
SALTS=$(curl -s https://api.wordpress.org/secret-key/1.1/salt/)
echo "$SALTS" >> wp-config.php

chown -R www-data:www-data /var/www/${DOMAIN_RMS}
chmod -R 755 /var/www/${DOMAIN_RMS}
echo -e "${GREEN}  ✅ WordPress installé${NC}"

# ── DOCKER (pour Bigdoc) ──
echo -e "\n${GREEN}[6/8] Installation Docker...${NC}"
apt-get install -y -qq ca-certificates curl gnupg
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
apt-get update -qq
apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin
systemctl enable docker
echo -e "${GREEN}  ✅ Docker installé${NC}"

# Préparer le répertoire Bigdoc
mkdir -p ${BIGDOC_DIR}/data
echo -e "${GREEN}  ✅ Répertoire Bigdoc créé : ${BIGDOC_DIR}${NC}"
echo -e "${YELLOW}  → Copiez vos fichiers Bigdoc dans ${BIGDOC_DIR}${NC}"
echo -e "${YELLOW}  → Configurez ${BIGDOC_DIR}/.env avant de lancer Docker${NC}"

# ── NGINX CONFIG ──
echo -e "\n${GREEN}[7/8] Configuration Nginx...${NC}"

# Config WordPress (realmedservices.com)
cat > /etc/nginx/sites-available/${DOMAIN_RMS} << NGINX_RMS
server {
    listen 80;
    server_name ${DOMAIN_RMS} www.${DOMAIN_RMS};

    root /var/www/${DOMAIN_RMS};
    index index.php index.html;

    # Gzip
    gzip on;
    gzip_types text/plain text/css application/json application/javascript text/xml application/xml text/javascript;

    # Cache fichiers statiques
    location ~* \.(jpg|jpeg|png|gif|ico|css|js|woff|woff2)$ {
        expires 30d;
        add_header Cache-Control "public, no-transform";
    }

    location / {
        try_files \$uri \$uri/ /index.php?\$args;
    }

    location ~ \.php$ {
        fastcgi_pass unix:/run/php/php8.2-fpm.sock;
        fastcgi_index index.php;
        include fastcgi_params;
        fastcgi_param SCRIPT_FILENAME \$realpath_root\$fastcgi_script_name;
    }

    location ~ /\.ht { deny all; }

    # Sécurité WordPress
    location = /xmlrpc.php { deny all; }
    location ~* /wp-config.php { deny all; }
}
NGINX_RMS

# Config Bigdoc (bigdoc.fr)
cat > /etc/nginx/sites-available/${DOMAIN_BIGDOC} << NGINX_BIGDOC
server {
    listen 80;
    server_name ${DOMAIN_BIGDOC} www.${DOMAIN_BIGDOC};

    # Rate limiting
    limit_req_zone \$binary_remote_addr zone=bigdoc:10m rate=10r/m;

    location / {
        limit_req zone=bigdoc burst=20 nodelay;
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 120s;
        proxy_connect_timeout 10s;
    }

    # Webhook Stripe (pas de rate limit)
    location /api/stripe-webhook {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }
}
NGINX_BIGDOC

# Activer les sites
ln -sf /etc/nginx/sites-available/${DOMAIN_RMS} /etc/nginx/sites-enabled/
ln -sf /etc/nginx/sites-available/${DOMAIN_BIGDOC} /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

nginx -t && systemctl reload nginx
echo -e "${GREEN}  ✅ Nginx configuré${NC}"

# ── SSL CERTBOT ──
echo -e "\n${GREEN}[8/8] SSL avec Certbot...${NC}"
apt-get install -y -qq certbot python3-certbot-nginx

echo ""
echo -e "${YELLOW}Avant de lancer Certbot, assurez-vous que :${NC}"
echo "  1. ${DOMAIN_RMS} pointe vers ce serveur (IP: $(curl -s ifconfig.me))"
echo "  2. ${DOMAIN_BIGDOC} pointe vers ce serveur"
echo ""
read -p "DNS configurés ? Lancer Certbot ? (oui/N) " ssl_confirm

if [[ "$ssl_confirm" == "oui" ]]; then
  certbot --nginx \
    -d ${DOMAIN_RMS} -d www.${DOMAIN_RMS} \
    -d ${DOMAIN_BIGDOC} -d www.${DOMAIN_BIGDOC} \
    --email ${WP_ADMIN_EMAIL} \
    --agree-tos --non-interactive

  # Renouvellement automatique
  (crontab -l 2>/dev/null; echo "0 3 * * * certbot renew --quiet") | crontab -
  echo -e "${GREEN}  ✅ SSL activé + renouvellement automatique${NC}"
fi

# ── CRON HEALTHCHECK ──
(crontab -l 2>/dev/null; echo "0 7 * * * curl -s https://${DOMAIN_BIGDOC}/api/health > /var/log/bigdoc-health.log 2>&1") | crontab -

# ── RÉSUMÉ ──
echo ""
echo -e "${GREEN}═══════════════════════════════════════════${NC}"
echo -e "${GREEN}  INSTALLATION TERMINÉE${NC}"
echo -e "${GREEN}═══════════════════════════════════════════${NC}"
echo ""
echo "  WordPress   → https://${DOMAIN_RMS}"
echo "  Bigdoc      → https://${DOMAIN_BIGDOC}"
echo "  Admin WP    → https://${DOMAIN_RMS}/wp-admin"
echo ""
echo -e "${YELLOW}  Identifiants WordPress :${NC}"
echo "  User  : ${WP_ADMIN_USER}"
echo "  Pass  : ${WP_ADMIN_PASS}"
echo ""
echo -e "${YELLOW}  Base de données :${NC}"
echo "  DB   : ${DB_NAME}"
echo "  User : ${DB_USER}"
echo "  Pass : ${DB_PASS}"
echo ""
echo -e "${YELLOW}  Étapes suivantes :${NC}"
echo "  1. Copiez Bigdoc dans ${BIGDOC_DIR}/"
echo "  2. Configurez ${BIGDOC_DIR}/.env"
echo "  3. cd ${BIGDOC_DIR} && docker compose up -d"
echo "  4. Terminez l'install WP sur https://${DOMAIN_RMS}"
echo "  5. Uploadez le thème rms-bigdoc dans WP Admin"
echo ""

# Sauvegarder les credentials
cat > /root/credentials.txt << CREDS
RMS VPS — Credentials
Date : $(date)

WordPress
─────────
URL Admin : https://${DOMAIN_RMS}/wp-admin
User      : ${WP_ADMIN_USER}
Pass      : ${WP_ADMIN_PASS}
Email     : ${WP_ADMIN_EMAIL}

Base de données
───────────────
DB Name   : ${DB_NAME}
DB User   : ${DB_USER}
DB Pass   : ${DB_PASS}

Bigdoc
──────
Répertoire : ${BIGDOC_DIR}
URL        : https://${DOMAIN_BIGDOC}
CREDS

chmod 600 /root/credentials.txt
echo -e "${GREEN}  Credentials sauvegardés dans /root/credentials.txt${NC}"
echo ""
