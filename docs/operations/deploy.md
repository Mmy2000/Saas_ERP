# Deploying Gweb, from A to Z

From a domain name and an empty server to a running platform with HTTPS, backups off the
server and monitoring. Then: releasing, rolling back and everyday care. The scripts are in
[`deploy/`](../../deploy); this page says when to run each one. Day-to-day operations are in the
[runbook](README.md).

```mermaid
flowchart LR
  U[Shops and staff] -- "https://*.your-domain.com" --> CF[Cloudflare DNS<br/>DNS only]
  CF --> N
  subgraph VPS [One Ubuntu 24.04 server]
    N[nginx<br/>TLS, /static/] --> G[gweb-web<br/>gunicorn]
    G --> P[(PostgreSQL 16)]
    W[gweb-worker<br/>jobs, nightly backup] --> P
    M[gweb-monitor] --> P
    WA[gweb-wa-gateway<br/>Node]
    W --> WA
    T[gweb-offsite.timer<br/>rclone, encrypted]
  end
  W -- SMTP --> E[Your e-mail provider]
  T --> S3[(R2 / B2 bucket)]
  UR[Uptime monitor] -- "/readyz/" --> N
```

Everything runs on one server, the design the code assumes: one PostgreSQL database for every
shop, row-level security, a file cache shared by the processes. A bigger server scales it a long
way. Several servers need a shared cache first (see [Limits](#limits-of-this-setup)).

## A. What you need before you start

| Thing | Notes | Rough cost |
|---|---|---|
| A domain | e.g. `gweb-erp.com`. Shops become `shop1.gweb-erp.com`, the console `admin.gweb-erp.com` | ~$10/year |
| A Cloudflare account | free; it runs the domain's DNS so the wildcard HTTPS certificate renews by itself | free |
| A server (VPS) | **Ubuntu 24.04**, 2 vCPU, 4 GB RAM, 40 GB SSD to start (Hetzner CX22/CPX21, DigitalOcean, Contabo…); 8 GB RAM past ~30 busy shops | $5–25/month |
| Off-site storage | a **Cloudflare R2** bucket (same account as the DNS) or Backblaze B2 | cents per GB |
| An e-mail sending service | SMTP from Brevo, Mailgun, Amazon SES, Zoho, your mail host… with your domain verified (SPF/DKIM) | free tier to a few $ |
| An uptime monitor | UptimeRobot, Better Stack… (free plans are enough) | free |
| A password manager | for the encryption keys, the backup password, the API tokens | — |
| An SSH key on your computer | `ssh-keygen -t ed25519` if you have none | — |

Throughout this page, replace `your-domain.com` with your domain and `203.0.113.10` with your
server's IP address.

## B. Put the domain's DNS on Cloudflare

1. **Add the domain.** In Cloudflare: **Add a site**, enter the domain, choose the **Free** plan.
2. **Point the registrar at Cloudflare.** At the registrar where you bought the domain, replace
   the nameservers with the two Cloudflare shows you. The change takes from minutes to a day;
   Cloudflare e-mails you when the domain is active.
3. **Add the DNS records.** In **DNS → Records**, add these after creating the server (step C),
   when you know its IP:

   | Type | Name | Content | Proxy status |
   |---|---|---|---|
   | A | `*` | `203.0.113.10` | **DNS only** (grey cloud) |
   | A | `admin` | `203.0.113.10` | **DNS only** |
   | AAAA | `*` and `admin` | the server's IPv6 address, if it has one | **DNS only** |

   Keep the records **DNS only**: requests then reach nginx directly. Through Cloudflare's proxy
   (orange cloud), every visitor would appear to come from a Cloudflare address, which breaks
   the per-address sign-in lock and the sign-in history. The bare domain (`your-domain.com`)
   can point at your website, or wherever you like.
4. **Create the API token certbot uses** for the certificate. **My Profile → API Tokens →
   Create Token → "Edit zone DNS"** template:
   - **Zone Resources:** Include → Specific zone → your domain.
   - **Create Token**, and copy it into the password manager. Cloudflare shows it once.

## C. Create the server

1. **Create a VPS** with **Ubuntu 24.04 LTS** and your SSH key, then note its IPv4 (and IPv6)
   address. Now add the DNS records from step B.3.
2. **Sign in and create your own admin user.** Root sign-in is turned off in step 3.

   ```bash
   ssh root@203.0.113.10
   adduser deploy && usermod -aG sudo deploy
   rsync --archive --chown=deploy:deploy ~/.ssh /home/deploy
   ```

3. **Turn off password and root sign-in over SSH.** From another terminal, first check that
   `ssh deploy@203.0.113.10` works. Then:

   ```bash
   sudo sed -i -E 's/^#?(PasswordAuthentication|PermitRootLogin) .*/\1 no/' /etc/ssh/sshd_config
   sudo systemctl restart ssh
   ```

   From here on, work as `deploy` with `sudo`.

## D. The settings file and the first script

```bash
sudo mkdir -p /etc/gweb
sudo curl -fsSL https://raw.githubusercontent.com/Mmy2000/Saas_ERP/main/deploy/deploy.conf.example \
     -o /etc/gweb/deploy.conf
sudo nano /etc/gweb/deploy.conf      # DOMAIN, ADMIN_EMAIL; the rest can stay
```

| Setting | Set it to |
|---|---|
| `DOMAIN` | `your-domain.com` |
| `ADMIN_EMAIL` | your address: Let's Encrypt notices and server error alerts |
| `WA_GATEWAY` | `yes` if shops will send WhatsApp from their own numbers |
| `OFFSITE_REMOTE` | keep `gweb-offsite:` (created in step F) |

Then prepare the server (about 5–10 minutes):

```bash
curl -fsSL https://raw.githubusercontent.com/Mmy2000/Saas_ERP/main/deploy/bootstrap.sh -o bootstrap.sh
sudo bash bootstrap.sh
```

It installs:
- Python 3.13, PostgreSQL 16, nginx, certbot with the Cloudflare plugin, rclone, Node.js 20
  (WhatsApp gateway only).
- A firewall (SSH, HTTP, HTTPS only).
- 2 GB of swap.
- Automatic security updates, and fail2ban for SSH.

It also creates the `gweb` user, and clones the code into `/srv/gweb`.

> The repository is public, so the server clones it over HTTPS with no key. If you make it
> private: as `gweb`, run `ssh-keygen -t ed25519`. Add `~/.ssh/id_ed25519.pub` as a read-only
> **Deploy key** in GitHub → Settings → Deploy keys. Then set
> `REPO_URL=git@github.com:Mmy2000/Saas_ERP.git` and run
> `sudo -u gweb git -C /srv/gweb remote set-url origin "$REPO_URL"`.

## E. The Cloudflare token for the certificate

```bash
sudo install -d -m 700 /etc/letsencrypt
echo 'dns_cloudflare_api_token = PASTE-THE-TOKEN-HERE' | sudo tee /etc/letsencrypt/cloudflare.ini >/dev/null
sudo chmod 600 /etc/letsencrypt/cloudflare.ini
```

## F. Off-site backups (encrypted)

The server makes a full backup every night at 02:30 (Cairo time). This step copies each one to
a bucket, encrypted on the server first: the storage company only ever sees ciphertext.

1. **Create a bucket** and a key that can write to it.
   - **Cloudflare R2:** R2 → Create bucket `gweb-backups`. Then **Manage R2 API Tokens →
     Create API token**, with **Object Read & Write** on that bucket. Note the **Access Key ID**,
     the **Secret Access Key** and the **endpoint** (`https://<account-id>.r2.cloudflarestorage.com`).
   - **Backblaze B2 instead:** create a private bucket `gweb-backups`, and an application key
     restricted to it. Note the **keyID** and the **applicationKey**.
2. **Make up two long passwords** for the encryption, and save both in the password manager.
   **Without them the backups can't be decrypted.**
3. **Configure rclone as the `gweb` user.**

   ```bash
   # R2:
   sudo -u gweb -H rclone config create gweb-store s3 provider=Cloudflare \
       access_key_id=ACCESS_KEY_ID secret_access_key=SECRET_ACCESS_KEY \
       endpoint=https://ACCOUNT_ID.r2.cloudflarestorage.com acl=private
   # (or B2:  sudo -u gweb -H rclone config create gweb-store b2 account=KEY_ID key=APPLICATION_KEY)

   # The encrypted layer on top (names and contents encrypted):
   sudo -u gweb -H rclone config create gweb-offsite crypt remote=gweb-store:gweb-backups/gweb \
       password="$(rclone obscure 'FIRST-LONG-PASSWORD')" \
       password2="$(rclone obscure 'SECOND-LONG-PASSWORD')"
   sudo -u gweb -H rclone lsd gweb-store:gweb-backups && echo "storage reachable"
   ```

`install.sh` switches the nightly copy on when it finds the `gweb-offsite` remote. If you do
this step later, run `install.sh` again afterwards.

## G. Install the platform

```bash
sudo bash /srv/gweb/deploy/install.sh
```

What it does, in order (about 5 minutes):

1. **Folders.** Only nginx's static files are readable outside the `gweb` user.
2. **Python environment:** the packages from `requirements/base.txt`.
3. **`/srv/gweb/.env`.** Every secret is generated once: Django's key, the two encryption keys,
   the three database passwords, the WhatsApp gateway key. Readable by `gweb` only.
4. **PostgreSQL:** the roles `app_owner`, `app_rw` and `app_platform`, and the `gweb` database
   (`ops/db/bootstrap.sql`).
5. **`gweb-manage`:** `manage.py` with production settings.
6. **Schema and static files:** the migrations, then `collectstatic`.
7. **Chromium** for the PDFs.
8. **WhatsApp gateway packages.**
9. **Services and timers:** web, worker, monitor, WhatsApp gateway, the traffic clean-up, and
   the off-site copy.
10. **The wildcard certificate** for `your-domain.com` and `*.your-domain.com`.
11. **nginx.**
12. **A first backup**, then a check of `https://admin.your-domain.com/readyz/`.

**Right away, save the two encryption keys** in the password manager. They are not in the
backups, and without them customers' national IDs can never be read again:

```bash
sudo grep -E '^(FIELD_ENCRYPTION_KEY|BLIND_INDEX_KEY)=' /srv/gweb/.env
```

`install.sh` is safe to run again at any time. It keeps the `.env`, the database and the
certificate, and re-applies the rest.

## H. E-mail

```bash
sudo -u gweb nano /srv/gweb/.env
#   EMAIL_URL=smtp+tls://USER:PASSWORD@smtp.your-provider.com:587
#   DEFAULT_FROM_EMAIL=no-reply@your-domain.com   (an address your provider lets you send from)
sudo systemctl restart gweb-web gweb-worker
gweb-manage sendtestemail you@example.com
```

URL-encode special characters in the password (`@` → `%40`, `:` → `%3A`, `/` → `%2F`). Without
e-mail, documents, reminders and error alerts fail: they show as failed jobs in Console → Jobs.

## I. Your staff account and the first shop

```bash
gweb-manage createsuperuser      # your e-mail and a long, unique password
```

1. **Open the console.** Go to `https://admin.your-domain.com/` and sign in.
2. **Create the first shop.** In **Clients → New**, enter the company, the address
   (`shop1` becomes `https://shop1.your-domain.com`), the owner's name, username, e-mail and
   password, and the plan.
3. **Sign in to the shop.** Open `https://shop1.your-domain.com/` and sign in with the owner's
   username.

Use a different e-mail for each shop's owner than for your staff account: a staff login can't
own a workspace ([ADR-022](../decisions/ADR-022-identity-across-workspaces.md)).

## J. The uptime monitor

In UptimeRobot (or similar), create an **HTTP(s)** monitor:

- **URL:** `https://admin.your-domain.com/readyz/`
- **Interval:** 5 minutes.
- **Alert when:** the status isn't 200.

It goes red when the database is down, the background worker is dead, or the last good backup
is more than 26 hours old. Add a second monitor on `https://shop1.your-domain.com/login/` for
the shops' side.

## K. Check that everything works

```bash
systemctl status gweb-web gweb-worker gweb-monitor gweb-wa-gateway --no-pager | grep -E '●|Active'
systemctl list-timers 'gweb-*' 'certbot*' --no-pager
curl -s https://admin.your-domain.com/readyz/; echo
sudo systemctl start gweb-offsite && journalctl -u gweb-offsite -n 5 --no-pager
sudo certbot renew --dry-run
```

In the browser:

- [ ] The console opens with a valid certificate; Console → Jobs shows a live worker.
- [ ] Console → Backups lists the first backup.
- [ ] In the shop: create an invoice and print it as PDF (this tests Chromium).
- [ ] Send a document by e-mail.
- [ ] WhatsApp: in the shop's settings, link a number by scanning the QR code.

## L. The first restore drill (within the first month, then monthly)

A backup nobody has restored is a hope, not a backup. This restores the newest one into a
scratch database **on the same server**, counts the rows, and throws it away:

```bash
sudo -u postgres createuser --superuser restore_drill
sudo -u postgres psql -c "ALTER ROLE restore_drill PASSWORD 'drill-only-temporary'"
sudo -u postgres createdb -O restore_drill restore_test
gweb-manage verify_backup --into postgres://restore_drill:drill-only-temporary@127.0.0.1:5432/restore_test
sudo -u postgres dropdb restore_test && sudo -u postgres dropuser restore_drill
```

It needs free disk space of about the database's size. Write the date and the result in the
[runbook's restore table](README.md#backups-and-restore). To prove the off-site copy as well,
fetch one from the bucket first (`sudo -u gweb -H rclone copy gweb-offsite:backups/<file>
/tmp/`), then run `--file /tmp/<file>`.

## M. Releasing a new version

After the change is merged to `main` and CI passed:

```bash
sudo bash /srv/gweb/deploy/release.sh
```

1. **A backup first.**
2. **The newest commit of `main`.**
3. **Packages, only if they changed:** `pip` (and Chromium when Playwright changed), and the
   WhatsApp gateway's `npm`.
4. **Migrations and static files.**
5. **Restart:** a graceful reload of the web (no dropped requests), and a restart of the worker
   and monitor.
6. **Wait for `/readyz/`.**

Each release is logged in `/srv/gweb/var/releases.log`, and the script prints the command to go
back. To deploy a specific commit or tag: `release.sh 4f2c1ab`. If a release changes files under
`deploy/systemd` or `deploy/nginx`, the script says so: run `install.sh` to apply them.

### Rolling back

```bash
sudo bash /srv/gweb/deploy/release.sh <previous commit>      # printed by the release
```

That puts the old code back. **It doesn't undo migrations.** If the release added migrations
the old code can't run with:

1. Reverse them first: `gweb-manage migrate <app> <previous migration> --database owner`.
2. Then roll back the code.
3. Or, if the data is wrong, restore the backup the release made
   ([runbook](README.md#rolling-back)).

## N. Everyday care

| Task | How |
|---|---|
| See the logs | `journalctl -u gweb-web -f` (or `gweb-worker`, `gweb-wa-gateway`, `nginx`); server errors also in Console → Errors |
| Restart something | `sudo systemctl restart gweb-worker` (etc.); the web without dropping requests: `sudo systemctl reload gweb-web` |
| Change a setting | `sudo -u gweb nano /srv/gweb/.env`, then restart web and worker |
| Run a management command | `gweb-manage <command>`: always production settings, as `gweb` |
| Reset a shared login's password | `gweb-manage changepassword their@email` ([ADR-022](../decisions/ADR-022-identity-across-workspaces.md)) |
| Certificates | renewed by `certbot.timer`, nginx reloaded after; test with `sudo certbot renew --dry-run` |
| Operating system updates | security updates install by themselves; monthly: `sudo apt update && sudo apt full-upgrade`, then `sudo reboot` if `/var/run/reboot-required` exists |
| Disk space | `df -h`; the backups are in `/srv/gweb/var/backups` (newest 14 kept; `BACKUP_KEEP` in `.env`) |
| Grow the server | resize the VPS, then set `WEB_WORKERS` in `/etc/gweb/deploy.conf` (or leave empty: 2 × cores + 1) and run `install.sh` |

## Custom domains for a shop

A shop can have its own domain (Console → the client → Domains, e.g. `www.goldshop.com`). The
wildcard certificate doesn't cover it, so it needs its own:

1. **DNS.** The shop points the domain at the server: an A record to `203.0.113.10`.
2. **Certificate.** nginx already answers Let's Encrypt's HTTP check on port 80:

   ```bash
   sudo certbot certonly --webroot -w /var/www/letsencrypt -d www.goldshop.com \
        --deploy-hook "systemctl reload nginx"
   ```

3. **nginx.** Copy the HTTPS `server { … }` block of `/etc/nginx/sites-available/gweb` into
   `/etc/nginx/sites-available/goldshop`.
   - Change `server_name` to `www.goldshop.com`.
   - Change the two `ssl_certificate` lines to `/etc/letsencrypt/live/www.goldshop.com/…`.
   - Remove `default_server`.
4. **Enable it.** Run `sudo ln -s /etc/nginx/sites-available/goldshop /etc/nginx/sites-enabled/`,
   then `sudo nginx -t && sudo systemctl reload nginx`.
5. **Allowed hosts.** Add the domain to `DJANGO_ALLOWED_HOSTS` in `.env` (comma-separated), then
   restart the web.

## Troubleshooting

| Symptom | Look at | Usually |
|---|---|---|
| `install.sh` stops at the certificate | the certbot error | the token lacks *Zone → DNS → Edit* for this zone, or the domain isn't active on Cloudflare yet |
| Browser: "site can't be reached" | `dig +short shop1.your-domain.com` | DNS records missing, or still propagating |
| 400 Bad Request on every page | `journalctl -u gweb-web -n 50` | host not in `DJANGO_ALLOWED_HOSTS` (e.g. a custom domain) |
| A shop's address shows "not found" | Console → Clients | no shop with that address, or it is suspended |
| `/readyz/` says `worker` failing | `journalctl -u gweb-worker -n 100` | the worker crashed or can't reach the database |
| `/readyz/` says `backup` failing | Console → Backups (error text) | disk full, or `pg_dump` and the server version disagree after a PostgreSQL upgrade |
| PDFs fail | `journalctl -u gweb-web -n 100` | Chromium missing after an upgrade: run `install.sh` |
| E-mails don't arrive | Console → Jobs (failed), `gweb-manage sendtestemail` | wrong `EMAIL_URL`, unencoded password, or the provider refuses the sender |
| "Too many wrong passwords" | — | lifts within 15 minutes ([runbook](README.md#incidents)) |
| Off-site copy fails | `journalctl -u gweb-offsite -n 50` | expired storage key, or the remote's name differs from `OFFSITE_REMOTE` |

## Limits of this setup

- **One server.** If it goes down, the platform is down until it's restarted or restored
  elsewhere. Recovery is: a new server, then steps C–G with the same `.env` (keep it in the
  password manager too), then a restore of the newest backup ([L](#l-the-first-restore-drill-within-the-first-month-then-monthly), into the real
  `gweb` database). Practise it once.
- **Several web servers** would need a shared cache (`CACHE_URL`) for the sign-in locks and
  feature switches, and shared media storage. Neither is set up.
- **Platform staff have no second factor yet** (TD-25). Use long, unique passwords.
- **Shops' own domains** need the manual steps above.
