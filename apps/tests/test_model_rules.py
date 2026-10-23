"""Checks that the schema enforces the business rules of Data Model v0.1."""

from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase

from apps.billing.models import BillableItem, BillingDraft, DraftLine, ExportedRecord, AccountingExport, Payment, PaymentAllocation
from apps.catalogue.models import Product, TaxCategory
from apps.crm.models import Account, Contact
from apps.crm.search import normalise, to_greeklish
from apps.platform.models import Sequence, Tenant, User
from apps.projects.models import Project, Task, TimeEntry
from apps.sales.models import Quote
from apps.subscriptions.models import Domain, Subscription
from apps.support.models import Department, Ticket
from apps.validators import validate_greek_vat


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_tenant", "WEDEVA", "wedeva", verbosity=0)
        cls.t = Tenant.objects.get(slug="wedeva")
        cls.user = User.objects.create_user("ako", email="ako@example.com", password="x" * 12, tenant=cls.t)
        cls.vat = TaxCategory.objects.get(tenant=cls.t, rate=Decimal("24.00"))
        cls.acc = Account.objects.create(tenant=cls.t, legal_name="Πελάτης Α.Ε.", tax_id="094014201")
        cls.product = Product.objects.create(
            tenant=cls.t, code="HOST-S", name="Small Hosting", type="service",
            charging_unit="month", tax_category=cls.vat, default_price=Decimal("10.00"),
        )
        cls.sub = Subscription.objects.create(
            tenant=cls.t, account=cls.acc, product=cls.product, agreed_price=Decimal("10.00"),
            start_date=date(2026, 1, 31), billing_anchor_day=31, next_billing_date=date(2026, 2, 28),
            status="active",
        )


class GreekRules(Base):
    def test_afm_check_digit(self):
        validate_greek_vat("094014201")
        validate_greek_vat("EL094014201")
        with self.assertRaises(ValidationError):
            validate_greek_vat("094014202")
        with self.assertRaises(ValidationError):
            validate_greek_vat("12345")

    def test_account_clean_validates_afm_for_greece(self):
        bad = Account(tenant=self.t, legal_name="X", tax_id="123456789", country="GR")
        with self.assertRaises(ValidationError):
            bad.full_clean()

    def test_search_ignores_accents_and_case(self):
        self.assertEqual(normalise("ΠΕΛΆΤΗΣ  Α.Ε."), "πελατης α.ε.")
        self.assertEqual(self.acc.search_name, "πελατης α.ε.")
        self.assertEqual(to_greeklish("Κορωνιός"), "koronios")

    def test_seed_created_greek_vat_rates(self):
        rates = set(TaxCategory.objects.filter(tenant=self.t).values_list("rate", flat=True))
        self.assertTrue({Decimal("24.00"), Decimal("13.00"), Decimal("6.00")} <= rates)


class BillingRules(Base):
    def item(self, **kw):
        defaults = dict(
            tenant=self.t, account=self.acc, source="subscription", description="Hosting",
            unit_price=Decimal("10.00"), tax_category=self.vat, subscription=self.sub,
            period_start=date(2026, 2, 28), period_end=date(2026, 3, 30),
        )
        defaults.update(kw)
        return BillableItem.objects.create(**defaults)

    def test_one_item_per_subscription_period(self):
        self.item()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.item()
        self.item(period_start=date(2026, 3, 31))  # next period is fine

    def test_one_item_per_domain_period(self):
        dom = Domain.objects.create(tenant=self.t, account=self.acc, domain_name="Example.GR", price=Decimal("15"))
        self.assertEqual(dom.domain_name, "example.gr")
        self.item(source="domain", subscription=None, domain=dom, period_start=date(2027, 1, 1))
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.item(source="domain", subscription=None, domain=dom, period_start=date(2027, 1, 1))

    def test_invoiced_draft_needs_external_reference(self):
        d = BillingDraft.objects.create(tenant=self.t, account=self.acc, number="BD-00001")
        d.status = "invoiced"
        with self.assertRaises(IntegrityError), transaction.atomic():
            d.save()
        d.external_invoice_no = "ΤΠΥ-123"
        d.external_invoice_date = date(2026, 3, 1)
        d.save()

    def test_shared_item_split_across_projects_and_payment_status(self):
        p1 = Project.objects.create(tenant=self.t, name="Server Alpha", type="hosting")
        p2 = Project.objects.create(tenant=self.t, name="Website", type="software", customer=self.acc)
        item = self.item()
        d = BillingDraft.objects.create(tenant=self.t, account=self.acc, number="BD-00002")
        for project, price in ((p1, Decimal("6.00")), (p2, Decimal("5.00"))):  # more than the item: allowed
            line = DraftLine(draft=d, billable_item=item, project=project, description="Hosting",
                             unit_price=price, tax_category=self.vat)
            line.compute()
            line.save()
        d.recalculate()
        self.assertEqual(d.net, Decimal("11.00"))
        self.assertEqual(d.gross, Decimal("13.64"))
        pay = Payment.objects.create(tenant=self.t, account=self.acc, date=date(2026, 3, 5), amount=Decimal("10.00"))
        PaymentAllocation.objects.create(payment=pay, draft=d, amount=Decimal("10.00"))
        d.recalculate()
        self.assertEqual(d.payment_status, "partial")
        self.assertEqual(d.outstanding, Decimal("3.64"))

    def test_financial_records_cannot_be_deleted(self):
        d = BillingDraft.objects.create(tenant=self.t, account=self.acc)
        with self.assertRaises(ValidationError):
            d.delete()

    def test_record_exported_only_once(self):
        exp = AccountingExport.objects.create(tenant=self.t, file_key="x.csv", exported_by=self.user)
        d = BillingDraft.objects.create(tenant=self.t, account=self.acc)
        ExportedRecord.objects.create(tenant=self.t, export=exp, record_type="draft", record_id=d.pk)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ExportedRecord.objects.create(tenant=self.t, export=exp, record_type="draft", record_id=d.pk)


class ProjectAndSupportRules(Base):
    def test_time_entry_needs_task_or_ticket_not_both(self):
        task = Task.objects.create(tenant=self.t, title="Migrate site")
        dept = Department.objects.get(tenant=self.t)
        ticket = Ticket.objects.create(tenant=self.t, subject="Down", account=self.acc, department=dept)
        TimeEntry.objects.create(tenant=self.t, user=self.user, task=task, date=date(2026, 3, 1), minutes=30)
        with self.assertRaises(IntegrityError), transaction.atomic():
            TimeEntry.objects.create(
                tenant=self.t, user=self.user, task=task, ticket=ticket, date=date(2026, 3, 1), minutes=30
            )

    def test_unapproved_time_cannot_be_billed(self):
        task = Task.objects.create(tenant=self.t, title="Support")
        item = BillableItem.objects.create(
            tenant=self.t, account=self.acc, source="time", description="Support 1h",
            unit_price=Decimal("50"), tax_category=self.vat,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            TimeEntry.objects.create(
                tenant=self.t, user=self.user, task=task, date=date(2026, 3, 1), minutes=60, billable_item=item
            )

    def test_ticket_sla_due_times(self):
        dept = Department.objects.get(tenant=self.t)
        ticket = Ticket.objects.create(tenant=self.t, subject="Slow", account=self.acc, department=dept, priority="high")
        ticket.apply_sla()
        self.assertEqual((ticket.response_due - ticket.created_at).total_seconds(), 3600)

    def test_quote_needs_account_or_lead(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Quote.objects.create(tenant=self.t, subject="Offer")

    def test_numbering_and_soft_delete(self):
        first = Sequence.next_number(self.t, "ticket")
        second = Sequence.next_number(self.t, "ticket")
        self.assertEqual((first, second), ("T-00001", "T-00002"))
        c = Contact.objects.create(tenant=self.t, first_name="Maria", email="m@example.com")
        c.delete()
        self.assertFalse(Contact.objects.active().filter(pk=c.pk).exists())
        self.assertTrue(Contact.objects.filter(pk=c.pk).exists())

    def test_gdpr_anonymise_keeps_record(self):
        c = Contact.objects.create(tenant=self.t, first_name="Maria", email="m@example.com")
        c.anonymise()
        c.refresh_from_db()
        self.assertEqual((c.first_name, c.email), ("Erased", ""))
        self.assertIsNotNone(c.anonymised_at)
