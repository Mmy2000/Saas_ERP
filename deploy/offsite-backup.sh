#!/usr/bin/env bash
# Copy the nightly backups and the WhatsApp sessions off the server (gweb-offsite.timer, as the
# app user). OFFSITE_REMOTE is an rclone remote, encrypted with rclone crypt
# (docs/operations/deploy.md), so the storage provider only sees ciphertext.
#
# Not copied on purpose: .env and the encryption keys. Keep those in a password manager: a
# leak of the bucket alone must not open the data.
set -euo pipefail
# shellcheck source=/dev/null
source /etc/gweb/deploy.conf
[[ -n ${OFFSITE_REMOTE:-} ]] || { echo "OFFSITE_REMOTE is empty: nothing to do."; exit 0; }
remote=${OFFSITE_REMOTE%/}
keep=${OFFSITE_KEEP_DAYS:-60}

# New dumps and media archives; the server keeps BACKUP_KEEP of them, the remote keeps $keep days.
rclone copy "$APP_DIR/var/backups" "$remote/backups" --include 'gweb-*' --min-age 1m \
    --transfers 2 --retries 5 --low-level-retries 20 --stats-one-line --stats 0
rclone delete "$remote/backups" --min-age "${keep}d"

if [[ -d $APP_DIR/var/wa-sessions ]]; then
    # A mirror: losing it means every shop scans its WhatsApp QR code again.
    rclone sync "$APP_DIR/var/wa-sessions" "$remote/wa-sessions" --retries 5
fi
echo "Off-site copy done: $(rclone lsf "$remote/backups" --files-only | wc -l) files on $remote"
