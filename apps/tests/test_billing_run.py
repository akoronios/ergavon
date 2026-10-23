"""Billing run behaviour (SUB-02, business rules 1 to 7)."""

from datetime import date
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from apps.billing.billing_run import retry_run, run_billing
from apps.billing.models import BillableItem, BillingDraft
from apps.billing.periods import add_months, period_for
from apps.catalogue.models import Product, TaxCategory
from apps.crm.models import Account
from apps.platform.models import Tenant
from apps.projects.models import Project, ProjectSubscription
from apps.subscriptions.models import Domain, ScheduledRun, Subscription, SubscriptionPrice


class PeriodMaths(TestCase):
    def test_anchor_31_clamps_and_returns(self):
        d = date(2026, 1, 31)
        seen = []
        for _ in range(4):
            d = add_months(d, 1, 31)
            seen.append(d)
        self.assertEqual(seen, [date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30), date(2026, 5, 31)])

    def test_leap_year_february(self):
        self.assertEqual(add_months(date(2028, 1, 30), 1, 30), date(2028, 2, 29))

    def test_periods_by_recurrence(self):
        self.assertEqual(period_for(date(2026, 1, 15), "monthly", 15), (date(2026, 1, 15), date(2026, 2, 14)))
        self.assertEqual(period_for(date(2026, 1, 1), "quarterly", 1), (date(2026, 1, 1), date(2026, 3, 31)))
        self.assertEqual(period_for(date(2026, 11, 30), "annual", 30), (date(2026, 11, 30), date(2027, 11, 29)))


class BillingRun(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_tenant", "WEDEVA", "wedeva", verbosity=0)
        cls.t = Tenant.objects.get(slug="wedeva")
        cls.vat = TaxCategory.objects.get(tenant=cls.t, rate=Decimal("24.00"))
        cls.a = Account.objects.create(tenant=cls.t, legal_name="Client A")
        cls.b = Account.objects.create(tenant=cls.t, legal_name="Client B")
        cls.hosting = Product.objects.create(
            tenant=cls.t, code="HOST", name="Hosting", type="service", charging_unit="month",
            tax_category=cls.vat, default_price=Decimal("10"),
        )
        cls.saas = Product.objects.create(
            tenant=cls.t, code="SAAS", name="SaaS seat", type="saas", charging_unit="month",
            tax_category=cls.vat, default_price=Decimal("20"),
        )

    def sub(self, account=None, product=None, start=date(2026, 1, 1), anchor=1, recurrence="monthly",
            price="10.00", status="active", **kw):
        return Subscription.objects.create(
            tenant=self.t, account=account or self.a, product=product or self.hosting,
            agreed_price=Decimal(price), start_date=start, billing_anchor_day=anchor,
            recurrence=recurrence, next_billing_date=start, status=status, **kw,
        )

    def test_bills_in_advance_and_moves_next_date(self):
        s = self.sub()
        result = run_billing(self.t, until=date(2026, 1, 1))
        self.assertEqual(result.items_created, 1)
        s.refresh_from_db()
        self.assertEqual(s.next_billing_date, date(2026, 2, 1))
        item = BillableItem.objects.get(subscription=s)
        self.assertEqual((item.period_start, item.period_end), (date(2026, 1, 1), date(2026, 1, 31)))
        self.assertEqual(item.status, "on_draft")
        self.assertEqual(result.run.status, ScheduledRun.Status.SUCCEEDED)

    def test_rerun_creates_nothing_new(self):
        self.sub()
        run_billing(self.t, until=date(2026, 1, 1))
        again = run_billing(self.t, until=date(2026, 1, 1))
        self.assertEqual((again.items_created, again.drafts_created, again.lines_created), (0, 0, 0))
        self.assertEqual(BillableItem.objects.count(), 1)
        self.assertEqual(BillingDraft.objects.count(), 1)

    def test_catch_up_bills_each_missed_period_once(self):
        self.sub()
        result = run_billing(self.t, until=date(2026, 3, 15))
        starts = list(BillableItem.objects.order_by("period_start").values_list("period_start", flat=True))
        self.assertEqual(starts, [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)])
        self.assertEqual(result.drafts_created, 1)

    def test_month_end_anchor_over_a_quarter(self):
        self.sub(start=date(2026, 1, 31), anchor=31)
        run_billing(self.t, until=date(2026, 4, 30))
        ends = list(BillableItem.objects.order_by("period_start").values_list("period_start", "period_end"))
        self.assertEqual(ends, [
            (date(2026, 1, 31), date(2026, 2, 27)),
            (date(2026, 2, 28), date(2026, 3, 30)),
            (date(2026, 3, 31), date(2026, 4, 29)),
            (date(2026, 4, 30), date(2026, 5, 30)),
        ])

    def test_one_draft_per_customer_with_a_line_per_subscription(self):
        self.sub()
        self.sub(product=self.saas, price="20.00")
        self.sub(account=self.b)
        result = run_billing(self.t, until=date(2026, 1, 1))
        self.assertEqual(result.drafts_created, 2)
        draft_a = BillingDraft.objects.get(account=self.a)
        self.assertEqual(draft_a.lines.count(), 2)
        self.assertEqual(draft_a.net, Decimal("30.00"))
        self.assertEqual(draft_a.gross, Decimal("37.20"))
        self.assertEqual(draft_a.payment_status, "unpaid")
        self.assertTrue(draft_a.number.startswith("BD-"))
        self.assertEqual((draft_a.due_date - draft_a.issue_date).days, 14)

    def test_suspended_skips_periods_and_resumes_without_back_billing(self):
        s = self.sub(status="suspended")
        run_billing(self.t, until=date(2026, 2, 15))
        self.assertEqual(BillableItem.objects.count(), 0)
        s.refresh_from_db()
        self.assertEqual(s.next_billing_date, date(2026, 3, 1))
        s.status = "active"
        s.save()
        run_billing(self.t, until=date(2026, 3, 1))
        self.assertEqual(list(BillableItem.objects.values_list("period_start", flat=True)), [date(2026, 3, 1)])

    def test_overdue_holds_then_catches_up_when_active(self):
        s = self.sub(status="overdue_payment")
        run_billing(self.t, until=date(2026, 2, 1))
        self.assertEqual(BillableItem.objects.count(), 0)
        s.refresh_from_db()
        self.assertEqual(s.next_billing_date, date(2026, 1, 1))
        s.status = "active"
        s.save()
        run_billing(self.t, until=date(2026, 2, 1))
        self.assertEqual(BillableItem.objects.count(), 2)

    def test_overdue_kept_active_keeps_billing(self):
        self.sub(status="overdue_payment", keep_active_when_overdue=True)
        run_billing(self.t, until=date(2026, 1, 1))
        self.assertEqual(BillableItem.objects.count(), 1)

    def test_cancellation_stops_periods_after_effective_date(self):
        self.sub(status="cancelled", cancel_effective_date=date(2026, 2, 10))
        run_billing(self.t, until=date(2026, 4, 1))
        starts = list(BillableItem.objects.order_by("period_start").values_list("period_start", flat=True))
        self.assertEqual(starts, [date(2026, 1, 1), date(2026, 2, 1)])

    def test_price_change_applies_from_next_period(self):
        s = self.sub()
        SubscriptionPrice.objects.create(subscription=s, price=Decimal("12.00"), valid_from=date(2026, 2, 1))
        run_billing(self.t, until=date(2026, 2, 1))
        prices = list(BillableItem.objects.order_by("period_start").values_list("unit_price", flat=True))
        self.assertEqual(prices, [Decimal("10.00"), Decimal("12.00")])

    def test_draft_status_subscription_not_billed(self):
        self.sub(status="draft")
        run_billing(self.t, until=date(2026, 3, 1))
        self.assertEqual(BillableItem.objects.count(), 0)

    def test_project_tag_only_when_single_project(self):
        one = self.sub()
        two = self.sub(product=self.saas)
        p1 = Project.objects.create(tenant=self.t, name="Server Alpha", type="hosting")
        p2 = Project.objects.create(tenant=self.t, name="Website", type="software", customer=self.a)
        ProjectSubscription.objects.create(project=p1, subscription=one)
        ProjectSubscription.objects.create(project=p1, subscription=two)
        ProjectSubscription.objects.create(project=p2, subscription=two)
        run_billing(self.t, until=date(2026, 1, 1))
        self.assertEqual(BillableItem.objects.get(subscription=one).project, p1)
        self.assertIsNone(BillableItem.objects.get(subscription=two).project)

    def test_domain_renewal_billed_once_per_term(self):
        d = Domain.objects.create(
            tenant=self.t, account=self.a, domain_name="example.gr", registration_date=date(2025, 3, 10),
            next_due_date=date(2026, 3, 10), price=Decimal("15.00"), renewal_years=2, status="active",
        )
        first = run_billing(self.t, until=date(2026, 3, 31))
        self.assertEqual(first.errors, [])
        run_billing(self.t, until=date(2026, 3, 31))
        item = BillableItem.objects.get(domain=d)
        self.assertEqual((item.period_start, item.period_end), (date(2026, 3, 10), date(2028, 3, 9)))
        d.refresh_from_db()
        self.assertEqual(d.next_due_date, date(2028, 3, 10))

    def test_domain_without_auto_renew_is_skipped(self):
        Domain.objects.create(
            tenant=self.t, account=self.a, domain_name="old.gr", next_due_date=date(2026, 1, 5),
            price=Decimal("15"), auto_renew=False, status="active",
        )
        run_billing(self.t, until=date(2026, 2, 1))
        self.assertEqual(BillableItem.objects.count(), 0)

    def test_error_on_one_subscription_does_not_block_others(self):
        good = self.sub()
        bad = self.sub(account=self.b)
        Subscription.objects.filter(pk=bad.pk).update(recurrence="weekly")  # invalid value, bypassing choices
        result = run_billing(self.t, until=date(2026, 1, 1))
        self.assertEqual(result.run.status, ScheduledRun.Status.FAILED)
        self.assertEqual(len(result.errors), 1)
        self.assertTrue(BillableItem.objects.filter(subscription=good).exists())
        self.assertFalse(BillableItem.objects.filter(subscription=bad).exists())

    def test_retry_is_recorded_and_idempotent(self):
        self.sub()
        first = run_billing(self.t, until=date(2026, 1, 1))
        again = retry_run(first.run, user=None)
        self.assertEqual(again.run.retry_of, first.run)
        self.assertEqual(BillableItem.objects.count(), 1)

    def test_later_run_reuses_open_draft_for_same_date_only(self):
        self.sub()
        run_billing(self.t, until=date(2026, 1, 1))
        run_billing(self.t, until=date(2026, 2, 1))
        self.assertEqual(BillingDraft.objects.count(), 2)

    def test_command(self):
        self.sub()
        out = StringIO()
        call_command("run_billing", "--tenant", "wedeva", "--until", "2026-01-01", stdout=out)
        self.assertIn("1 items, 1 drafts", out.getvalue())
