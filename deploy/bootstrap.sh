#!/usr/bin/env bash
# Step 1 on a fresh Ubuntu 24.04 server, as root: system packages, the app user, firewall,
# swap and automatic security updates. Safe to run again.
#
#   sudo bash bootstrap.sh            (reads /etc/gweb/deploy.conf)
set -euo pipefail

CONF=/etc/gweb/deploy.conf
[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)." >&2; exit 1; }
[[ -f $CONF ]] || { echo "Create $CONF first (from deploy/deploy.conf.example)." >&2; exit 1; }
# shellcheck source=/dev/null
source "$CONF"
. /etc/os-release
[[ ${VERSION_ID:-} == "24.04" ]] || echo "Warning: written for Ubuntu 24.04, this is ${PRETTY_NAME:-unknown}."

export DEBIAN_FRONTEND=noninteractive
echo "== Packages"
apt-get update -q
apt-get -y -q full-upgrade
apt-get install -y -q software-properties-common curl ca-certificates gnupg
# Python 3.13, as in development and CI (Ubuntu 24.04 ships 3.12).
add-apt-repository -y ppa:deadsnakes/ppa
apt-get update -q
apt-get install -y -q git build-essential python3.13 python3.13-venv python3.13-dev \
    postgresql postgresql-contrib nginx certbot python3-certbot-dns-cloudflare rclone \
    ufw fail2ban unattended-upgrades
if [[ ${WA_GATEWAY:-no} == yes ]] && ! node --version 2>/dev/null | grep -qE '^v(2[0-9]|[3-9][0-9])\.'; then
    echo "== Node.js 20 (WhatsApp gateway)"
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
    apt-get install -y -q nodejs
fi

echo "== The app user ($APP_USER) and folder ($APP_DIR)"
id "$APP_USER" >/dev/null 2>&1 || adduser --system --group --home "/home/$APP_USER" \
    --shell /bin/bash "$APP_USER"
install -d -o "$APP_USER" -g "$APP_USER" -m 750 "$APP_DIR"
chmod 755 /etc/gweb && chmod 644 "$CONF"

# In a container (a test run) the host owns the firewall and swap.
in_container=no
systemd-detect-virt --container --quiet && in_container=yes

echo "== Firewall: SSH, HTTP, HTTPS only"
if [[ $in_container == no ]]; then
    ufw allow OpenSSH >/dev/null
    ufw allow 'Nginx Full' >/dev/null
    ufw --force enable >/dev/null
fi

echo "== Swap (Chromium makes the PDFs and needs memory)"
if [[ $in_container == no ]] && ! swapon --show | grep -q .; then
    fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap -q /swapfile && swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "== Automatic security updates, fail2ban for SSH"
dpkg-reconfigure -f noninteractive unattended-upgrades
systemctl enable --now fail2ban >/dev/null

echo "== The code ($REPO_URL, $BRANCH)"
if [[ ! -d $APP_DIR/.git ]]; then
    sudo -u "$APP_USER" -H git clone --quiet --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi

echo "Done. Next: sudo bash $APP_DIR/deploy/install.sh (docs/operations/deploy.md)."
