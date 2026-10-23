"""Create a tenant with its roles, Greek VAT categories, SLA targets and numbering.

Usage: python manage.py seed_tenant "WEDEVA" wedeva

The VAT rates below are seed values: confirm them with the accountant
before the first billing run.
"""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.catalogue.models import TaxCategory
from apps.platform.models import Role, Sequence, Tenant
from apps.support.models import Department, SlaPolicy

GREEK_VAT = [
    ("ΦΠΑ 24%", "24.00", TaxCategory.Kind.STANDARD, "", True),
    ("ΦΠΑ 13%", "13.00", TaxCategory.Kind.REDUCED, "", False),
    ("ΦΠΑ 6%", "6.00", TaxCategory.Kind.SUPER_REDUCED, "", False),
    ("ΦΠΑ 17% (νησιά)", "17.00", TaxCategory.Kind.ISLAND, "", False),
    ("ΦΠΑ 9% (νησιά)", "9.00", TaxCategory.Kind.ISLAND, "", False),
    ("ΦΠΑ 4% (νησιά)", "4.00", TaxCategory.Kind.ISLAND, "", False),
    ("Απαλλαγή ΦΠΑ", "0.00", TaxCategory.Kind.EXEMPT, "EXEMPT", False),
    ("Αντίστροφη επιβάρυνση (ΕΕ)", "0.00", TaxCategory.Kind.REVERSE_CHARGE, "", False),
]

# Placeholder SLA targets in minutes: first response, resolution
SLA = {"high": (60, 8 * 60), "medium": (4 * 60, 3 * 24 * 60), "low": (24 * 60, 10 * 24 * 60)}

SEQUENCES = {"quote": "Q-", "order": "ORD-", "draft": "BD-", "ticket": "T-", "project": "PRJ-"}


class Command(BaseCommand):
    help = "Create a tenant with default roles, Greek VAT categories, SLA targets and numbering."

    def add_arguments(self, parser):
        parser.add_argument("name")
        parser.add_argument("slug")

    @transaction.atomic
    def handle(self, name, slug, **options):
        tenant, created = Tenant.objects.get_or_create(slug=slug, defaults={"name": name})
        for role in Role.Name.values:
            Role.objects.get_or_create(tenant=tenant, name=role)
        for label, rate, kind, code, default in GREEK_VAT:
            TaxCategory.objects.get_or_create(
                tenant=tenant,
                name=label,
                defaults={"rate": Decimal(rate), "kind": kind, "exemption_code": code, "is_default": default},
            )
        for priority, (first, resolve) in SLA.items():
            SlaPolicy.objects.get_or_create(
                tenant=tenant,
                priority=priority,
                defaults={"first_response_minutes": first, "resolution_minutes": resolve},
            )
        Department.objects.get_or_create(tenant=tenant, name="Support", defaults={"is_default": True})
        for kind, prefix in SEQUENCES.items():
            Sequence.objects.get_or_create(tenant=tenant, kind=kind, defaults={"prefix": prefix})
        verb = "Created" if created else "Updated"
        if options["verbosity"]:
            self.stdout.write(self.style.SUCCESS(f"{verb} tenant {tenant.name} ({tenant.slug})"))
