"""Run billing for one tenant or all tenants.

Daily cron on the server, e.g. at 06:10 Athens time:
    10 6 * * * cd /path/to/ergavon && .venv/bin/python manage.py run_billing

Bill up to a given date (catch-up or a dry check on a copy):
    python manage.py run_billing --tenant wedeva --until 2026-11-30
"""

from datetime import date

from django.core.management.base import BaseCommand, CommandError

from apps.billing.billing_run import DEFAULT_PAYMENT_TERMS_DAYS, run_billing
from apps.platform.models import Tenant


class Command(BaseCommand):
    help = "Create billable items and billing drafts for every subscription period and domain renewal that is due."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", help="Tenant slug; all tenants when omitted")
        parser.add_argument("--until", type=date.fromisoformat, help="Bill periods starting on or before this date (YYYY-MM-DD); default today")
        parser.add_argument("--terms", type=int, default=DEFAULT_PAYMENT_TERMS_DAYS, help="Payment terms in days for new drafts")

    def handle(self, *args, tenant=None, until=None, terms=DEFAULT_PAYMENT_TERMS_DAYS, **options):
        tenants = Tenant.objects.all()
        if tenant:
            tenants = tenants.filter(slug=tenant)
            if not tenants.exists():
                raise CommandError(f"No tenant with slug {tenant!r}")
        failed = False
        for t in tenants:
            result = run_billing(t, until=until, payment_terms_days=terms)
            line = (
                f"{t.slug}: {result.items_created} items, {result.drafts_created} drafts, "
                f"{result.lines_created} lines, {len(result.errors)} errors (run {result.run.pk})"
            )
            if result.errors:
                failed = True
                self.stderr.write(self.style.ERROR(line))
                for err in result.errors:
                    self.stderr.write(f"  {err}")
            elif options["verbosity"]:
                self.stdout.write(self.style.SUCCESS(line))
        if failed:
            raise CommandError("Billing run finished with errors; see the run history")
