# Testing ERGAVON locally with Docker (Windows)

Runs the whole beta on your PC in three containers: PostgreSQL 16, the ERGAVON
web app, and the daily billing scheduler. There is nothing to install apart from
Docker Desktop and Git.

## 1. Install Docker Desktop (once)

1. Download **Docker Desktop for Windows** from docker.com and install it.
   Keep the **Use WSL 2** option ticked.
2. Restart Windows if the installer asks.
3. Start Docker Desktop and wait until it shows **Engine running**.
4. Check it in PowerShell:

   ```powershell
   docker --version
   docker compose version
   ```

## 2. Get the code

```powershell
cd $HOME\Documents
git clone https://github.com/akoronios/ergavon.git
cd ergavon
```

No Git? On GitHub use **Code → Download ZIP**, unzip it, and open PowerShell in
that folder.

## 3. Start ERGAVON

```powershell
docker compose up --build
```

The first time takes a few minutes while Docker downloads Python and
PostgreSQL. It is ready when the log shows a `Booting worker` line from
gunicorn.

On every start the web container:

- waits for the database;
- applies any new migrations;
- creates the WEDEVA tenant, its roles, Greek VAT categories, SLA targets and numbering;
- creates the first admin user, once.

## 4. Open it

Go to **http://localhost:8000** and sign in:

- Username: `admin`
- Password: `ergavon-local`

These credentials and every other value in `docker-compose.yml` are for your
PC only. Never reuse them on a server.

## 5. Try the main flow

1. **Products**: add a product (for example Hosting, type Service, charging unit Month, VAT 24%).
2. **Accounts**: add a customer. A Greek ΑΦΜ is checked on save; `123456783` is a valid made-up test value.
3. **Subscriptions**: add one for that customer and product. Set it to **Active**, with a
   start date and next billing date a few months back, billing anchor day = the start day.
4. **Scheduled runs → Run billing now**. You get one billable item per due month
   and one billing draft for the customer.
5. **Billing drafts**: open the draft, then click **Approve**, **Sent to accounting** and
   **Mark invoiced** (enter any number and date).
6. **Payments**: add a payment for the customer, then **Allocate to a draft** or
   **Auto-allocate**. The draft shows Partially paid or Paid.
7. **Audit log** (Settings): every step above is recorded with your user.

## Everyday commands

Run these from the `ergavon` folder.

| What | Command |
| --- | --- |
| Start in the background | `docker compose up -d` |
| See the logs | `docker compose logs -f web` |
| Stop (keeps your data) | `docker compose down` |
| Stop and **delete all data** | `docker compose down -v` |
| Run billing now from the terminal | `docker compose exec web python manage.py run_billing` |
| Bill up to a date | `docker compose exec web python manage.py run_billing --until 2026-12-31` |
| Run the test suite | `docker compose exec web python manage.py test apps` |
| Open a Django shell | `docker compose exec web python manage.py shell` |
| Get a new version | `git pull`, then `docker compose up --build -d` |

The scheduler container also runs billing by itself every day at 06:10 Athens
time while Docker is running. Its log: `docker compose logs -f scheduler`.

## If something goes wrong

| What you see | Fix |
| --- | --- |
| `docker` is not recognised | Docker Desktop is not installed or not running; start it and open a new PowerShell |
| `port is already allocated` on 8000 | Something else uses port 8000. In `docker-compose.yml` change `"8000:8000"` to `"8080:8000"`, then open http://localhost:8080 and add `http://localhost:8080` to `CSRF_TRUSTED_ORIGINS` |
| Login says CSRF verification failed | Open the site exactly as `http://localhost:8000`, not by IP or another name |
| Web container keeps restarting | `docker compose logs web` shows the reason; `docker compose down -v` then `up --build` starts clean |
| You want to start over with empty data | `docker compose down -v`, then `docker compose up --build` |
