"""Smoke tests for the Unfold admin: every page renders, the billing buttons work."""

from datetime import date
from decimal import Decimal

from django.contrib import admin
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from apps.billing.billing_run import run_billing
from apps.billing.models import BillingDraft, Payment
from apps.catalogue.models import Product, TaxCategory
from apps.crm.models import Account
from apps.platform.models import Tenant, User
from apps.subscriptions.models import ScheduledRun, Subscription


class AdminPages(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_tenant", "WEDEVA", "wedeva", verbosity=0)
        cls.t = Tenant.objects.get(slug="wedeva")
        cls.admin_user = User.objects.create_superuser("root", "root@example.com", "x" * 12, tenant=cls.t)
        vat = TaxCategory.objects.get(tenant=cls.t, rate=Decimal("24.00"))
        cls.acc = Account.objects.create(tenant=cls.t, legal_name="Πελάτης Α.Ε.")
        product = Product.objects.create(
            tenant=cls.t, code="HOST", name="Hosting", type="service", tax_category=vat, default_price=Decimal("100")
        )
        Subscription.objects.create(
            tenant=cls.t, account=cls.acc, product=product, agreed_price=Decimal("100"),
            start_date=date(2026, 1, 1), billing_anchor_day=1, next_billing_date=date(2026, 1, 1), status="active",
        )

    def setUp(self):
        self.client.force_login(self.admin_user)

    def test_dashboard_renders_figures(self):
        run_billing(self.t, until=date(2026, 1, 1))
        res = self.client.get(reverse("admin:index"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Drafts to approve")
        self.assertContains(res, "Last billing run")

    def test_every_changelist_and_add_page_renders(self):
        for model, model_admin in admin.site._registry.items():
            meta = model._meta
            url = reverse(f"admin:{meta.app_label}_{meta.model_name}_changelist")
            self.assertEqual(self.client.get(url).status_code, 200, url)
            if model_admin.has_add_permission(_req(self.admin_user)):
                add = reverse(f"admin:{meta.app_label}_{meta.model_name}_add")
                self.assertEqual(self.client.get(add).status_code, 200, add)

    def test_change_pages_render(self):
        run_billing(self.t, until=date(2026, 1, 1))
        draft = BillingDraft.objects.get()
        for name, pk in (("billing_billingdraft", draft.pk), ("crm_account", self.acc.pk)):
            res = self.client.get(reverse(f"admin:{name}_change", args=[pk]))
            self.assertEqual(res.status_code, 200, name)

    def test_account_search_ignores_accents(self):
        res = self.client.get(reverse("admin:crm_account_changelist") + "?q=ΠΕΛΑΤΗΣ")
        self.assertContains(res, "Πελάτης Α.Ε.")

    def test_draft_buttons_run_the_lifecycle(self):
        run_billing(self.t, until=date(2026, 1, 1))
        draft = BillingDraft.objects.get()
        self.client.get(reverse("admin:billing_billingdraft_approve_one", args=[draft.pk]))
        self.client.get(reverse("admin:billing_billingdraft_send_one", args=[draft.pk]))
        res = self.client.post(
            reverse("admin:billing_billingdraft_invoice_one", args=[draft.pk]),
            {"_form_submitted": "on", "external_invoice_no": "ΤΠΥ-1", "external_invoice_date": "2026-01-05"},
        )
        self.assertEqual(res.status_code, 302)
        draft.refresh_from_db()
        self.assertEqual((draft.status, draft.external_invoice_no), ("invoiced", "ΤΠΥ-1"))

    def test_payment_allocate_dialog(self):
        run_billing(self.t, until=date(2026, 1, 1))
        draft = BillingDraft.objects.get()
        self.client.get(reverse("admin:billing_billingdraft_approve_one", args=[draft.pk]))
        pay = Payment.objects.create(tenant=self.t, account=self.acc, date=date(2026, 1, 10), amount=Decimal("50"))
        form = self.client.get(reverse("admin:billing_payment_allocate_one", args=[pay.pk]))
        self.assertEqual(form.status_code, 200)
        self.client.post(
            reverse("admin:billing_payment_allocate_one", args=[pay.pk]),
            {"_form_submitted": "on", "draft": str(draft.pk), "amount": "50.00"},
        )
        draft.refresh_from_db()
        self.assertEqual((draft.paid, draft.payment_status), (Decimal("50.00"), "partial"))

    def test_run_billing_button(self):
        res = self.client.post(
            reverse("admin:subscriptions_scheduledrun_run_billing_now"),
            {"_form_submitted": "on", "tenant": str(self.t.pk), "until": "2026-02-01"},
        )
        self.assertEqual(res.status_code, 302)
        self.assertEqual(ScheduledRun.objects.get().status, "succeeded")
        self.assertEqual(BillingDraft.objects.count(), 1)


def _req(user):
    from django.test import RequestFactory

    request = RequestFactory().get("/")
    request.user = user
    return request
