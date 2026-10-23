"""Container start-up: wait for the database, prepare it, then serve ERGAVON.

Every step is safe to repeat, so restarting the container never duplicates data:
- migrations only apply what is missing
- the tenant seed uses get_or_create
- the first admin user is created only if no user with that name exists
"""

import os
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")


def wait_for_database(timeout=60):
    import django
    from django.db import connection
    from django.db.utils import OperationalError

    django.setup()
    deadline = time.monotonic() + timeout
    while True:
        try:
            connection.ensure_connection()
            return
        except OperationalError as exc:
            if time.monotonic() > deadline:
                sys.exit(f"Database not reachable after {timeout}s: {exc}")
            print("Waiting for the database...", flush=True)
            time.sleep(2)


def prepare():
    from django.core.management import call_command

    call_command("migrate", interactive=False, verbosity=1)

    tenant_name = os.environ.get("ERGAVON_TENANT_NAME", "WEDEVA")
    tenant_slug = os.environ.get("ERGAVON_TENANT_SLUG", "wedeva")
    call_command("seed_tenant", tenant_name, tenant_slug)

    username = os.environ.get("ERGAVON_ADMIN_USER")
    password = os.environ.get("ERGAVON_ADMIN_PASSWORD")
    if username and password:
        from apps.platform.models import Tenant, User

        if not User.objects.filter(username=username).exists():
            User.objects.create_superuser(
                username=username,
                email=os.environ.get("ERGAVON_ADMIN_EMAIL", f"{username}@example.com"),
                password=password,
                tenant=Tenant.objects.get(slug=tenant_slug),
            )
            print(f"Created admin user {username!r}", flush=True)


def serve():
    port = os.environ.get("PORT", "8000")
    workers = os.environ.get("WEB_CONCURRENCY", "3")
    os.execvp(
        "gunicorn",
        [
            "gunicorn",
            "config.wsgi:application",
            "--bind", f"0.0.0.0:{port}",
            "--workers", workers,
            "--access-logfile", "-",
            "--timeout", "120",
        ],
    )


if __name__ == "__main__":
    wait_for_database()
    if os.environ.get("ERGAVON_SKIP_PREPARE") != "1":
        prepare()
    if "--prepare-only" in sys.argv:
        sys.exit(0)
    subprocess.run([sys.executable, "manage.py", "check"], cwd=BASE_DIR, check=True)
    serve()
