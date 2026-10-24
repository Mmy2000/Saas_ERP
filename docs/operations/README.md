# Operations runbook

Running Gweb in production. Setting up a server is covered step by step in
[Deploying, from A to Z](deploy.md) (scripts in `deploy/`); this page covers keeping it
running. Everything here is what the code supports today. **Missing
safeguards are listed at the end**: read them before relying on this setup.

## Configuration

Every variable is listed, with its default, in `.env.example`.

**Required in production.** `config/settings/prod.py` refuses to start without the first three.

| Variable | Why |
|---|---|
| `DJANGO_SECRET_KEY` | sessions, signed links, mobile tokens |
| `FIELD_ENCRYPTION_KEY`, `BLIND_INDEX_KEY` | national IDs. **Keep a copy outside the server**: backups don't contain them |
| `DJANGO_SETTINGS_MODULE=config.settings.prod` | in the services' environment **and** in the shell of whoever runs `manage.py` |
| `DJANGO_ALLOWED_HOSTS`, `TENANT_BASE_DOMAIN`, `PLATFORM_HOSTS` | which hosts are clients and which are the console |
| `DATABASE_URL` (app_rw), `DATABASE_OWNER_URL` (app_owner), `BACKUP_DATABASE_URL` (app_platform) | the three roles from `ops/db/bootstrap.sql` |
| `EMAIL_URL`, `DEFAULT_FROM_EMAIL`, `SITE_SCHEME=https` | e-mail and the links in it |
| `ERROR_ALERT_EMAILS` | who hears about new server errors and failed jobs |

**Optional, by feature:**
- WhatsApp and SMS: `WHATSAPP_*`, `WA_GATEWAY_*`, `SMS_*`.
- Diamond lab reports: `IGI_*`.
- Backups: `BACKUP_DIR`, `BACKUP_KEEP`, `BACKUP_PG_DUMP`, `BACKUP_PG_RESTORE`.
- Several servers: `CACHE_URL`.
- Tuning: `TENANT_REQUESTS_PER_MINUTE`, `DB_CONN_MAX_AGE`, `MONITOR_*`.

Changing a variable means restarting the service that reads it: web, worker and monitor all
read the same `.env`.

## Processes

| Service | Command | If it stops |
|---|---|---|
| web | `gunicorn config.wsgi --bind 127.0.0.1:8000 --workers 4 --timeout 120` | the site is down |
| worker | `manage.py run_worker` | no e-mail, WhatsApp, reminders, market prices or **backups**; Console → Jobs shows no live worker after 60 s |
| monitor | `manage.py monitor_server` | Console → Server stops updating |
| WhatsApp gateway (optional) | `node ops/wa-gateway/server.mjs` | shops using their own number can't send |
| daily cron | `manage.py prune_traffic` | traffic tables grow |

Run them under systemd with `Restart=always`, as README "Production" describes. The worker is
the one to watch: **point an uptime monitor at `https://admin.<domain>/readyz/`**. It answers 503
when the database is unreachable, no worker is alive (seen in the last minute, or busy with a job
within its time limit), or the last good backup is over 26 hours old. A failed pass of the
worker (say, the database restarting) is logged as an error and retried; it doesn't stop it.

## Releasing a new version

There are no release tags or packaged builds yet. A release is a commit on `main` that passed
CI. **`sudo bash /srv/gweb/deploy/release.sh` does all of the steps below** ([deploy guide,
M](deploy.md#m-releasing-a-new-version)); they are listed so you know what it does, or can do
them by hand. On the server, as the `gweb` user in `/srv/gweb`:

1. **Back up first:** `venv/bin/python manage.py backup`. Note the file names it prints.
2. **Get the code:** `git fetch && git checkout <commit>`. Write down the commit you're leaving:
   it is your rollback target.
3. **Dependencies, only if `requirements/` changed:**
   `venv/bin/pip install -r requirements/base.txt`. Playwright upgrade: also
   `venv/bin/python -m playwright install --with-deps chromium`.
4. **Migrate:** `venv/bin/python manage.py migrate --database owner`. Read the release's
   migrations first: anything flagged in its pull request as slow or locking runs at a quiet
   hour.
5. **Static files:** `venv/bin/python manage.py collectstatic --noinput`. The CSS, icons and
   vendored files are committed, so nothing else is built on the server.
6. **Restart:** web, worker and monitor. Translations (`.mo`) are committed and load on restart.
7. **Check:**
   - `https://admin.<domain>/readyz/` answers 200 with every check `ok` (give the worker a
     minute after its restart).
   - Sign in to the console: Jobs shows a live worker, Errors shows nothing new.
   - Open one client's dashboard.

Browsers may keep old JS/CSS for a while, because static files aren't versioned (TD-22). Tell
users to refresh if a screen behaves oddly right after a release.

## Rolling back

- **Code only, no migrations in the release:** check out the previous commit, then restart the
  services.
- **With reversible migrations:**
  1. `manage.py migrate <app> <previous migration> --database owner` for each app the release
     migrated. The previous migration names are in the release's pull request.
  2. Check out the previous commit and restart.
  3. Data written by the new version in new columns is lost.
- **With an irreversible or destructive migration:** restore the backup taken in step 1 of the
  release (below). Everything written since is lost, so prefer fixing forward unless data is
  wrong.

Write a migration's rollback in its pull request before merging
([database standards](../standards/database.md#migrations)).

## Monitoring

| Where | What it tells you |
|---|---|
| Console → **Errors** | every server error and every job's last failed attempt, grouped, with traceback, client, user and page; new or returning ones are e-mailed to `ERROR_ALERT_EMAILS` |
| Console → **Jobs** | queued, running, failed jobs with their last error; whether a worker is alive |
| Console → **Backups** | every run, failed runs included; warns when the last good one is over a day old |
| Console → **Server** | CPU, memory, disk, network, PostgreSQL (from `monitor_server`) |
| Console → **Traffic** | requests per client, slow screens, clients near or over their limit |
| `journalctl -u gweb-web` (etc.) | the processes' logs: tracebacks, warnings (no timestamps inside the line: TD-17) |
| `/readyz/` (platform host) | **for the uptime monitor:** `{"status": "ok"\|"failing", "checks": {"database", "worker", "backup"}}`, 200 or 503 |
| `/healthz/` (platform host) | only that the web process answers |

## Backups and restore

- **Schedule.** Nightly at 02:30 the worker dumps the whole database (`pg_dump` as
  `app_platform`) and zips `MEDIA_ROOT` into `BACKUP_DIR`. The newest `BACKUP_KEEP` (14) of
  each are kept.
- **Manual run:** `manage.py backup`, or Console → Backups → Back up now.
- **Every backup is read back** before it counts as good: `pg_restore --list` reads the dump's
  table of contents, and the media archive's checksums are tested. A truncated or damaged
  dump shows as a failed run.
- **`manage.py verify_backup`** checks the newest good backup again (both files still there and
  readable).
- **Copy `BACKUP_DIR` off the server every day.** A backup on the same disk is lost with it.
  Nothing in the code does this.
- **Also back up separately:** the two encryption keys, `.env`, and `ops/wa-gateway/sessions/`
  if the gateway is used. Losing the sessions means re-linking those shops' WhatsApp.
- **To restore:** README "Backups and restoring" has the `pg_restore` and `unzip` commands.

  **A restore has never been rehearsed (TD-04).** Until it has, recovery time is unknown.
  Rehearse it on a scratch PostgreSQL server, never the live one:

  ```bash
  createdb -h scratch -U postgres restore_test
  venv/bin/python manage.py verify_backup --into postgres://postgres:…@scratch:5432/restore_test
  ```

  It restores the newest good backup, as a role that bypasses row-level security (the scratch
  server's superuser), and prints the rows of the main tables. It refuses a database the site
  uses. Note how long it took, then drop `restore_test`. Record each drill here (monthly):

  | Date | By | Backup file | Result |
  |---|---|---|---|
  | — | — | — | not yet done |

## Incidents

| Symptom | First checks | Action |
|---|---|---|
| Site down | `systemctl status gweb-web`, nginx, `journalctl -u gweb-web` | restart; if it fails at start, read the error (missing env variable, database unreachable) |
| Many 500s | Console → Errors (newest), journal | roll back the release if it started with it; otherwise fix forward |
| `/readyz/` says `worker` failing; no e-mails/WhatsApp, prices stale | Console → Jobs: worker alive? | `systemctl restart gweb-worker`; read the journal and Console → Errors for why it stopped |
| `/readyz/` says `backup` failing | Console → Backups (error text), disk space, `BACKUP_DATABASE_URL`, `pg_dump`/`pg_restore` installed | fix and run `manage.py backup` |
| One shop gets 429 | Console → Traffic → that client | anonymous traffic counts against the shop (TD-12); raise its limit in the console for now; find the source IP in the nginx log |
| One shop can't sign in | Console → that client: status (suspended?), user's membership | reactivate, or reset the member's password from the client's own Settings → Users |
| "Too many wrong passwords" | someone guessed, or a user forgot | it lifts within 15 minutes; it can't be lifted early except by clearing the cache (`var/cache`), which lifts every lock |
| An owner can't reset a member's password ("uses the same login outside this workspace") | that login also belongs to another shop, or to staff ([ADR-022](../decisions/ADR-022-identity-across-workspaces.md)) | the person changes it themselves; there's no self-service reset yet, so platform staff run `manage.py changepassword <their email>` on the server (with production settings exported) |
| Disk full | `df -h`; `BACKUP_DIR`, `var/media`, journal size, `var/cache` | lower `BACKUP_KEEP`, move backups off the server, vacuum the journal |
| Database slow | Console → Server (Postgres), `pg_stat_activity` | look for long transactions; a stuck `pg_dump` can be one |

After an incident, write down what happened and what would have warned earlier. Add a
[register](../tech-debt.md) entry for the missing safeguard.

## Missing safeguards

These are not in place yet (from the [audit](../audit/2026-10-engineering-audit.md)):

- **`/readyz/` must be watched by something outside the server.** The code provides the check;
  configure the uptime monitor.
- **No rehearsed restore, and no off-site copy built in** (TD-04).
- **No second factor for staff** (TD-25).
- **A job longer than its `stale_after` is still taken for abandoned.** The backup's is 2 hours;
  a new long task must set its own (TD-07's remaining edge).
- **Log lines have no timestamps or request ids** (TD-17).
- **Static files aren't cache-busted** (TD-22).
- **No release tags or changelog.** Tag releases (`vYYYY.MM.DD`) when deploying, so rollbacks
  have a name.
