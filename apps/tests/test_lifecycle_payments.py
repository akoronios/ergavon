"""Draft lifecycle, payments, credit and the audit log."""

from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.billing import lifecycle, payments
from apps.billing.billing_run import run_billing
from apps.billing.models import BillableItem, BillingDraft, DraftLine
from apps.catalogue.models import Product, TaxCategory
from apps.crm.models import Account
from apps.platform.audit import acting_as
from apps.platform.models import AuditLog, Role, Tenant, User, UserRole
from apps.subscriptions.models import Subscription


class Setup(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_tenant", "WEDEVA", "wedeva", verbosity=0)
        cls.t = Tenant.objects.get(slug="wedeva")
        cls.vat = TaxCategory.objects.get(tenant=cls.t, rate=Decimal("24.00"))
        cls.finance = User.objects.create_user("fin", email="fin@example.com", password="x" * 12, tenant=cls.t)
        UserRole.objects.create(user=cls.finance, role=Role.objects.get(tenant=cls.t, name="finance"))
        cls.sales = User.objects.create_user("sal", email="sal@example.com", password="x" * 12, tenant=cls.t)
        UserRole.objects.create(user=cls.sales, role=Role.objects.get(tenant=cls.t, name="salesperson"))
        cls.acc = Account.objects.create(tenant=cls.t, legal_name="Client A")
        cls.other = Account.objects.create(tenant=cls.t, legal_name="Client B")
        cls.product = Product.objects.create(
            tenant=cls.t, code="HOST", name="Hosting", type="service", charging_unit="month",
            tax_category=cls.vat, default_price=Decimal("100"),
        )

    def setUp(self):
        self.sub = Subscription.objects.create(
            tenant=self.t, account=self.acc, product=self.product, agreed_price=Decimal("100.00"),
            start_date=date(2026, 1, 1), billing_anchor_day=1, next_billing_date=date(2026, 1, 1), status="active",
        )
        run_billing(self.t, until=date(2026, 2, 1))  # two periods on one draft: net 200, gross 248
        self.draft = BillingDraft.objects.get(account=self.acc)

    def through_to(self, status):
        steps = [
            ("approved", lambda d: lifecycle.approve(d, self.finance)),
            ("sent", lambda d: lifecycle.send_to_accounting(d, self.finance)),
            ("invoiced", lambda d: lifecycle.mark_invoiced(d, self.finance, "ΤΠΥ-0042", date(2026, 2, 3))),
        ]
        d = self.draft
        for name, step in steps:
            d = step(d)
            if name == status:
                break
        return d


class Lifecycle(Setup):
    def test_full_flow_locks_and_bills_items(self):
        d = self.through_to("invoiced")
        self.assertEqual(d.status, "invoiced")
        self.assertEqual(d.approved_by, self.finance)
        self.assertEqual(set(BillableItem.objects.values_list("status", flat=True)), {"billed"})
        d.notes = "change"
        with self.assertRaises(ValidationError):
            d.save()

    def test_steps_must_follow_the_order(self):
        with self.assertRaises(ValidationError):
            lifecycle.send_to_accounting(self.draft, self.finance)
        with self.assertRaises(ValidationError):
            lifecycle.mark_invoiced(self.draft, self.finance, "X1", date(2026, 2, 3))

    def test_invoice_needs_number_and_date(self):
        d = self.through_to("sent")
        with self.assertRaises(ValidationError):
            lifecycle.mark_invoiced(d, self.finance, "  ", date(2026, 2, 3))

    def test_roles(self):
        with self.assertRaises(PermissionDenied):
            lifecycle.approve(self.draft, self.sales)
        with self.assertRaises(PermissionDenied):
            lifecycle.approve(self.draft, None)

    def test_lines_frozen_after_approval_and_editable_after_reopen(self):
        d = self.through_to("approved")
        line = d.lines.first()
        line.unit_price = Decimal("1")
        with self.assertRaises(ValidationError):
            line.save()
        d = lifecycle.reopen(d, self.finance)
        line.compute()
        line.save()
        d.recalculate()
        self.assertEqual(d.net, Decimal("101.00"))

    def test_empty_draft_cannot_be_approved(self):
        empty = BillingDraft.objects.create(tenant=self.t, account=self.other)
        with self.assertRaises(ValidationError):
            lifecycle.approve(empty, self.finance)

    def test_void_releases_items_and_reissue_picks_them_up(self):
        d = self.through_to("sent")
        d = lifecycle.void(d, self.finance, "wrong price")
        self.assertEqual(d.status, "voided")
        self.assertEqual(set(BillableItem.objects.values_list("status", flat=True)), {"uninvoiced"})
        new = lifecycle.reissue(d, self.finance)
        self.assertEqual(new.status, "draft")
        self.assertEqual(new.reissued_from, d)
        self.assertNotEqual(new.number, d.number)
        self.assertEqual(new.gross, Decimal("248.00"))
        self.assertEqual(set(BillableItem.objects.values_list("status", flat=True)), {"on_draft"})
        with self.assertRaises(ValidationError):
            lifecycle.reissue(d, self.finance)  # items already on the new draft

    def test_void_needs_reason_and_no_payments(self):
        with self.assertRaises(ValidationError):
            lifecycle.void(self.draft, self.finance, "")
        p = payments.record_payment(self.acc, "50", date(2026, 2, 5), self.finance)
        payments.allocate(p, self.draft, "50", self.finance)
        with self.assertRaises(ValidationError):
            lifecycle.void(self.draft, self.finance, "duplicate")

    def test_invoiced_draft_cannot_be_voided(self):
        d = self.through_to("invoiced")
        with self.assertRaises(ValidationError):
            lifecycle.void(d, self.finance, "late")


class Payments(Setup):
    def test_partial_then_full_payment(self):
        d = self.through_to("invoiced")
        p1 = payments.record_payment(self.acc, "100", date(2026, 2, 10), self.finance)
        payments.allocate(p1, d, "100", self.finance)
        d.refresh_from_db()
        self.assertEqual((d.paid, d.outstanding, d.payment_status), (Decimal("100.00"), Decimal("148.00"), "partial"))
        p2 = payments.record_payment(self.acc, "148", date(2026, 2, 20), self.finance)
        payments.allocate(p2, d, "148", self.finance)
        d.refresh_from_db()
        self.assertEqual((d.outstanding, d.payment_status), (Decimal("0.00"), "paid"))

    def test_allocation_limits(self):
        d = self.through_to("approved")
        p = payments.record_payment(self.acc, "300", date(2026, 2, 10), self.finance)
        with self.assertRaises(ValidationError):
            payments.allocate(p, d, "249", self.finance)  # more than outstanding
        small = payments.record_payment(self.acc, "10", date(2026, 2, 10), self.finance)
        with self.assertRaises(ValidationError):
            payments.allocate(small, d, "11", self.finance)  # more than the payment
        other = payments.record_payment(self.other, "10", date(2026, 2, 10), self.finance)
        with self.assertRaises(ValidationError):
            payments.allocate(other, d, "5", self.finance)  # another customer

    def test_overpayment_stays_as_credit_and_pays_the_next_draft(self):
        d = self.through_to("approved")
        p = payments.record_payment(self.acc, "300", date(2026, 2, 10), self.finance)
        payments.auto_allocate(p, self.finance)
        d.refresh_from_db()
        self.assertEqual(d.payment_status, "paid")
        self.assertEqual(payments.credit_balance(self.acc), Decimal("52.00"))
        run_billing(self.t, until=date(2026, 3, 1))
        march = BillingDraft.objects.filter(account=self.acc, status="draft").get()
        payments.apply_credit(self.acc, march, self.finance)
        march.refresh_from_db()
        self.assertEqual((march.paid, march.payment_status), (Decimal("52.00"), "partial"))
        self.assertEqual(payments.credit_balance(self.acc), Decimal("0.00"))

    def test_unallocate_returns_money_to_credit(self):
        d = self.through_to("approved")
        p = payments.record_payment(self.acc, "100", date(2026, 2, 10), self.finance)
        alloc = payments.allocate(p, d, "100", self.finance)
        payments.unallocate(alloc, self.finance, amount="40")
        d.refresh_from_db()
        self.assertEqual(d.paid, Decimal("60.00"))
        self.assertEqual(payments.credit_balance(self.acc), Decimal("40.00"))
        alloc.refresh_from_db()
        payments.unallocate(alloc, self.finance)
        d.refresh_from_db()
        self.assertEqual(d.payment_status, "unpaid")

    def test_overdue_status_follows_payment(self):
        d = self.through_to("sent")
        BillingDraft.objects.filter(pk=d.pk).update(due_date=timezone.localdate() - timedelta(days=1))
        payments.sync_overdue_status(self.t)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, "overdue_payment")
        p = payments.record_payment(self.acc, "248", timezone.localdate(), self.finance)
        payments.allocate(p, d, "248", self.finance)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, "active")

    def test_payment_must_be_positive(self):
        with self.assertRaises(ValidationError):
            payments.record_payment(self.acc, "0", date(2026, 2, 10), self.finance)


class Audit(Setup):
    def test_approval_is_logged_with_user_and_values(self):
        lifecycle.approve(self.draft, self.finance)
        entry = AuditLog.objects.filter(object_id=str(self.draft.pk), action="approve").get()
        self.assertEqual(entry.user, self.finance)
        self.assertEqual((entry.old_values["status"], entry.new_values["status"]), ("draft", "approved"))

    def test_status_change_and_update_logged(self):
        with acting_as(self.sales):
            self.acc.phone = "2101234567"
            self.acc.save()
        entry = AuditLog.objects.filter(object_id=str(self.acc.pk), action="update").latest("at")
        self.assertEqual(entry.user, self.sales)
        self.assertEqual(entry.new_values, {"phone": "2101234567"})
        self.assertEqual(entry.old_values, {"phone": ""})

    def test_create_and_line_level_records_logged(self):
        self.assertTrue(AuditLog.objects.filter(object_id=str(self.draft.pk), action="create").exists())
        line = DraftLine.objects.filter(draft=self.draft).first()
        self.assertTrue(AuditLog.objects.filter(object_id=str(line.pk), tenant=self.t).exists())

    def test_void_logged_with_reason(self):
        lifecycle.void(self.draft, self.finance, "wrong price")
        entry = AuditLog.objects.filter(object_id=str(self.draft.pk), action="status").latest("at")
        self.assertIn("wrong price", entry.note)
