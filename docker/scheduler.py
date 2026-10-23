"""Runs the daily billing run inside its own container.

Waits until the next BILLING_TIME (default 06:10, Europe/Athens), runs
`run_billing` for all tenants, and repeats. The run is idempotent, so a
container restart or a doubled run never bills a period twice.
"""

import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

ZONE = ZoneInfo(os.environ.get("BILLING_TIMEZONE", "Europe/Athens"))


def next_run(now: datetime, at: str) -> datetime:
    hour, minute = (int(x) for x in at.split(":"))
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return candidate if candidate > now else candidate + timedelta(days=1)


def main():
    import django
    from django.core.management import CommandError, call_command
    from django.db import close_old_connections

    django.setup()
    at = os.environ.get("BILLING_TIME", "06:10")
    print(f"Scheduler started: billing runs daily at {at} {ZONE.key}", flush=True)
    if os.environ.get("BILLING_RUN_ON_START") == "1":
        call_command("run_billing")
    while True:
        now = datetime.now(ZONE)
        target = next_run(now, at)
        print(f"Next billing run at {target:%Y-%m-%d %H:%M %Z}", flush=True)
        time.sleep(max(1, (target - now).total_seconds()))
        close_old_connections()
        try:
            call_command("run_billing")
        except CommandError as exc:
            print(f"Billing run finished with errors: {exc}", flush=True)
        except Exception as exc:  # keep the scheduler alive; the run history shows details
            print(f"Billing run failed: {exc!r}", flush=True)


if __name__ == "__main__":
    main()
