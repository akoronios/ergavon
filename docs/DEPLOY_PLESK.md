# Running ERGAVON on a Plesk server

Step-by-step setup of the ERGAVON beta on Plesk for Linux, served by Phusion
Passenger, with PostgreSQL. It assumes the app goes on a subdomain such as
`ergavon.wedeva.com` under the `wedeva.com` subscription. Replace both with
your own names everywhere below.

Plesk does not support Django officially; this follows Plesk's own knowledge
base article for Django under Passenger, adapted to ERGAVON.

---

## 0. Check the server first (5 minutes)

ERGAVON needs all of these. If you run your own VPS you can install what is
missing; on shared hosting ask the provider.

| Need | How to check | If missing |
| --- | --- | --- |
| Plesk for Linux | Plesk login page | Windows Plesk cannot run Passenger |
| SSH access for the subscription user, shell `/bin/bash` | Websites & Domains → wedeva.com → SSH Access | Ask the provider to enable it |
| Phusion Passenger | Tools & Settings → Updates → Add/Remove Components → Web hosting → Phusion Passenger | As root: `plesk installer --select-release-current --install-component passenger` |
| **Python 3.12 or newer** | Over SSH: `python3 --version` | Unfold, the admin theme, needs 3.12+. Ubuntu 24.04 ships 3.12; on older systems install a newer Python (root), or ask the provider |
| **PostgreSQL 14 or newer** | Tools & Settings → Database Servers shows PostgreSQL | Install via Plesk Installer, then click localhost next to PostgreSQL to set the admin password. Plesk manages PostgreSQL only on Web Pro and Web Host licences; Web Admin needs the Power Pack or Developer Pack |
| Git | Over SSH: `git --version` | `apt install git` (root) |

Django 5.2 refuses to run on PostgreSQL older than 14. Check the version with
`psql --version` over SSH.

---

## 1. Create the subdomain and its certificate

1. **Websites & Domains → Add Subdomain**: name `ergavon`, parent `wedeva.com`.
   Leave the document root as proposed for now; you change it in step 7.
2. On the new subdomain: **SSL/TLS Certificates → Install** a free Let's Encrypt certificate.
3. **Hosting & DNS → Hosting Settings**: tick **Permanent SEO-safe 301 redirect from HTTP to HTTPS**. Save.

## 2. Create the database

1. **Websites & Domains → Databases → Add Database**.
2. Database server: **PostgreSQL**. Name: `ergavon`.
3. Create a user `ergavon` with a strong password. Copy it; you need it in step 5.
   Avoid `@ : / ?` in the password, or URL-encode them later.

## 3. Get the code onto the server

Connect over SSH as the subscription user. Your home folder is
`/var/www/vhosts/wedeva.com`.

```bash
cd /var/www/vhosts/wedeva.com
git clone https://github.com/akoronios/ergavon.git ergavon
cd ergavon
```

If the repository is private, GitHub asks for credentials. Either use a
personal access token as the password, or add the server's SSH key as a
read-only deploy key under the repository's Settings → Deploy keys and clone
with `git@github.com:akoronios/ergavon.git`.

Keep the code in `~/ergavon`, outside `httpdocs`, so nothing in it is ever
served as a plain file.

## 4. Create the Python environment

```bash
python3 -m venv .venv          # use python3.12 explicitly if python3 is older
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

## 5. Write the settings file

```bash
cp .env.example .env
.venv/bin/python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
nano .env
chmod 600 .env
mkdir -p /var/www/vhosts/wedeva.com/ergavon-media
```

Fill `.env` like this, pasting the secret key printed above:

```ini
DEBUG=False
SECRET_KEY=<the printed key>
ALLOWED_HOSTS=ergavon.wedeva.com
CSRF_TRUSTED_ORIGINS=https://ergavon.wedeva.com
DATABASE_URL=postgres://ergavon:<db password>@localhost:5432/ergavon
MEDIA_ROOT=/var/www/vhosts/wedeva.com/ergavon-media
```

Leave `BEHIND_PROXY` and `SECURE_SSL_REDIRECT` unset: Plesk already redirects
to HTTPS, and turning them on under Passenger makes every page loop.

## 6. Set up the database and the first user

```bash
.venv/bin/python manage.py migrate
.venv/bin/python manage.py seed_tenant "WEDEVA" wedeva
.venv/bin/python manage.py createsuperuser
.venv/bin/python manage.py collectstatic --noinput
.venv/bin/python manage.py check --deploy
```

`check --deploy` may print a few warnings (for example about HSTS); none of
them stop the app. `seed_tenant` creates the roles, the Greek VAT categories,
placeholder SLA targets and numbering. Confirm the VAT rates with your
accountant before the first billing run.

## 7. Point the subdomain at the app

In Plesk, on **ergavon.wedeva.com**:

1. **Hosting & DNS → Hosting Settings → Document root**: `ergavon/public`. Save.
   This folder is empty on purpose; Django answers every request.
2. **Hosting & DNS → Apache & nginx Settings**:
   - Under nginx settings, **untick Proxy mode**.
   - In **Additional nginx directives** paste:

     ```nginx
     passenger_enabled on;
     passenger_app_type wsgi;
     passenger_startup_file passenger_wsgi.py;
     passenger_app_root /var/www/vhosts/wedeva.com/ergavon;
     passenger_python /var/www/vhosts/wedeva.com/ergavon/.venv/bin/python;
     ```
   - Click **OK**.

If your Plesk serves sites through Apache only (no nginx), Plesk's article
uses the Apache directive `PLESK_INFO: PassengerEnabled On` instead, with the
Passenger module switched on under Tools & Settings → Apache Web Server. The
nginx setup above is simpler; use it when nginx is available.

## 8. Open it

Go to `https://ergavon.wedeva.com`. It redirects to the admin; sign in with the
superuser from step 6. You should see the ERGAVON dashboard with the Beta label.

Create the real staff users under **Settings → Users**, set their tenant to
WEDEVA, and give each one a role (Admin, Finance, Operations, Manager or
Salesperson). Only Admin and Finance can approve drafts and record payments.

## 9. Schedule the daily billing run

**Websites & Domains → Scheduled Tasks → Add Task**:

- Task type: **Run a command**
- Command:

  ```bash
  cd /var/www/vhosts/wedeva.com/ergavon && .venv/bin/python manage.py run_billing
  ```
- Run: **Daily**, at 06:10 (Plesk uses the server's clock; check its timezone)
- Notify: **Errors only**, to your email

The run is safe to repeat, so a missed or doubled day never bills twice. Every
run appears under **Billing → Scheduled runs**, where an Admin can also start
one by hand with **Run billing now**.

## 10. Updating to a new version

After a pull request is merged on GitHub:

```bash
cd /var/www/vhosts/wedeva.com/ergavon
./deploy.sh
```

It pulls the code, installs requirements, migrates, collects static files and
restarts the app (by touching `tmp/restart.txt`).

## 11. Backups

Plesk's **Backup Manager** covers the subscription's files and its PostgreSQL
database. Include `~/ergavon/.env` and `~/ergavon-media`, and test a restore
once. The spec target is daily backups kept 30 days.

---

## Troubleshooting

| What you see | Likely cause | Fix |
| --- | --- | --- |
| 403 Forbidden | Passenger directives missing on the subdomain | Repeat step 7.2; check Proxy mode is off |
| "Web application could not be started" | Python error at start-up | Plesk → subdomain → Logs, or run `.venv/bin/python manage.py check` over SSH |
| 400 Bad Request | Host not allowed | `ALLOWED_HOSTS` in `.env` must be the exact subdomain |
| 403 CSRF failed when signing in | Origin not trusted | `CSRF_TRUSTED_ORIGINS=https://ergavon.wedeva.com` |
| Page keeps redirecting | `SECURE_SSL_REDIRECT` or `BEHIND_PROXY` turned on | Remove them from `.env`, then `touch tmp/restart.txt` |
| Admin has no styling | Static files not collected | `.venv/bin/python manage.py collectstatic --noinput`, then restart |
| Database connection refused | Wrong `DATABASE_URL` | Check user, password, database name; URL-encode special characters |
| Code changes not visible | Passenger still runs the old copy | `touch tmp/restart.txt` (or `./deploy.sh`) |

Source: Plesk, "How to install Django applications in Plesk" (support article
12377516625559) and "How to install and enable PostgreSQL database server in
Plesk" (12377866155031).
