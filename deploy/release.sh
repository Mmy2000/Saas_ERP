#!/usr/bin/env bash
# Deploy a version, as root. Without an argument: the newest commit of BRANCH on GitHub.
#
#   sudo bash /srv/gweb/deploy/release.sh              # origin/main
#   sudo bash /srv/gweb/deploy/release.sh 4f2c1ab      # a commit or tag (also: to roll back)
#
# Backs up first, then: code, dependencies (only when they changed), migrations, static files,
# a graceful reload, and waits for /readyz/. Every release is logged in var/releases.log.
# Rolling back with an older commit does NOT reverse migrations: see docs/operations/README.md.
set -euo pipefail

CONF=/etc/gweb/deploy.conf
die() { echo "ERROR: $*" >&2; exit 1; }
step() { echo; echo "== $*"; }
[[ $EUID -eq 0 ]] || die "Run as root (sudo)."
# This file is part of the code it checks out: run from a copy, or bash would read the new
# version halfway through.
if [[ ${GWEB_RELEASE_COPY:-} != 1 ]]; then
    copy=$(mktemp /tmp/gweb-release.XXXXXX)
    cp "$0" "$copy"
    GWEB_RELEASE_COPY=1 exec bash "$copy" "$@"
fi
trap 'rm -f "$0"' EXIT
# shellcheck source=/dev/null
source "$CONF"
cd "$APP_DIR"
as_app() { sudo -u "$APP_USER" -H "$@"; }
git_app() { as_app git -C "$APP_DIR" "$@"; }

[[ -z $(git_app status --porcelain --untracked-files=no) ]] \
    || die "The code on the server was edited by hand (git status). Undo that first."
target=${1:-origin/$BRANCH}

step "Backup before anything changes"
gweb-manage backup

step "Code"
previous=$(git_app rev-parse HEAD)
git_app fetch --quiet --tags origin
new=$(git_app rev-parse --verify --quiet "$target^{commit}") || die "Unknown version: $target"
if [[ $new == "$previous" ]]; then
    echo "Already at $(git_app log -1 --format='%h %s' "$new")."
fi
git_app checkout --quiet --detach "$new"
echo "$(git_app log -1 --format='%h %s' "$previous")  ->  $(git_app log -1 --format='%h %s' "$new")"
changed() { [[ -n $(git_app diff --name-only "$previous" "$new" -- "$@") ]]; }

if changed requirements/; then
    step "Python packages"
    as_app venv/bin/pip install --quiet -r requirements/base.txt
    if changed requirements/base.txt && git_app diff "$previous" "$new" -- requirements/base.txt \
            | grep -q '^[+-]playwright=='; then
        venv/bin/python -m playwright install-deps chromium >/dev/null
        as_app env PLAYWRIGHT_BROWSERS_PATH="$APP_DIR/var/ms-playwright" \
            venv/bin/python -m playwright install chromium
    fi
fi
if [[ ${WA_GATEWAY:-no} == yes ]] && changed ops/wa-gateway/package-lock.json; then
    step "WhatsApp gateway packages"
    (cd ops/wa-gateway && as_app npm ci --omit=dev --no-audit --no-fund --silent)
fi

step "Migrations and static files"
gweb-manage migrate --database owner --noinput
gweb-manage collectstatic --noinput -v 0

if changed deploy/systemd/ deploy/nginx/; then
    echo
    echo "NOTE: deploy/systemd or deploy/nginx changed: run install.sh to apply them."
fi

step "Restart (graceful for the web)"
systemctl reload gweb-web
systemctl restart gweb-worker gweb-monitor
if [[ ${WA_GATEWAY:-no} == yes ]] && changed ops/wa-gateway/; then
    systemctl restart gweb-wa-gateway
fi
install -m 755 "$APP_DIR/deploy/gweb-manage" /usr/local/bin/gweb-manage

printf '%s  %s -> %s  by %s\n' "$(date -u +%FT%TZ)" "$previous" "$new" "${SUDO_USER:-root}" \
    | as_app tee -a "$APP_DIR/var/releases.log" >/dev/null

step "Ready?"
for _ in $(seq 1 40); do
    if curl -fs --resolve "admin.$DOMAIN:443:127.0.0.1" "https://admin.$DOMAIN/readyz/" \
        -o /dev/null; then
        echo "Released $(git_app log -1 --format='%h' "$new"); /readyz/ answers ok."
        echo "To go back: sudo bash $APP_DIR/deploy/release.sh $previous"
        exit 0
    fi
    sleep 3
done
curl -sS --resolve "admin.$DOMAIN:443:127.0.0.1" "https://admin.$DOMAIN/readyz/" || true
echo
die "/readyz/ is not ok after 2 minutes. Check: journalctl -u gweb-web -u gweb-worker -n 100. To go back: sudo bash $APP_DIR/deploy/release.sh $previous"
