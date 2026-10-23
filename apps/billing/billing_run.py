"""The scheduled billing run (SUB-02.6 to 02.8, business rules 1 to 7).

A run bills, in advance, every subscription period and domain renewal that
starts on or before the run's `until` date, then groups each customer's new
billable items into one billing draft.

Guarantees:
- idempotent: re-running or retrying adds nothing already billed, because a
  billable item is unique per (subscription, period_start) and per
  (domain, period_start), and the next billing date only moves forward
  together with the item it produced
- catch-up: a subscription whose runs were missed gets one item per missed
  period, oldest first
- isolation: an error on one subscription or domain is logged on the run and
  never blocks the others
- one run at a time per tenant (row lock on the tenant)
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from django.db import transaction
from django.utils import timezone

from apps.billing.models import BillableItem, BillingDraft, DraftLine
from apps.billing.periods import domain_period_for, period_for
from apps.catalogue.models import TaxCategory
from apps.platform.models import Sequence, Tenant
from apps.subscriptions.models import Domain, ScheduledRun, Subscription

DEFAULT_PAYMENT_TERMS_DAYS = 14
MAX_PERIODS_PER_RECORD = 120  # safety net for a bad start date


@dataclass
class RunResult:
    run: ScheduledRun
    items_created: int = 0
    drafts_created: int = 0
    lines_created: int = 0
    errors: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------- eligibility


def subscription_bills_period(sub: Subscription, period_start: date) -> bool:
    """Whether this subscription produces an item for the period starting on `period_start`."""
    if period_start < sub.start_date:
        return False
    if sub.cancel_effective_date and period_start > sub.cancel_effective_date:
        return False  # SUB-02.3
    if sub.status == Subscription.Status.ACTIVE:
        return True
    if sub.status == Subscription.Status.OVERDUE:
        return sub.keep_active_when_overdue
    if sub.status == Subscription.Status.CANCELLED:
        # Periods that start before the cancellation takes effect are still billed
        return bool(sub.cancel_effective_date) and period_start <= sub.cancel_effective_date
    return False  # Draft, Suspended (SUB-02.2), Expired


def price_on(sub: Subscription, period_start: date):
    """Agreed price, or the latest price change valid from this period (SUB-01.5)."""
    change = sub.prices.filter(valid_from__lte=period_start).order_by("-valid_from").first()
    return change.price if change else sub.agreed_price


def single_project(sub: Subscription):
    """Tag the draft line with the project only when there is exactly one (rule 8)."""
    projects = list(sub.projects.all()[:2])
    return projects[0] if len(projects) == 1 else None


# ---------------------------------------------------------------- item creation


def bill_subscription(sub_id, until: date, result: RunResult) -> list[BillableItem]:
    created = []
    with transaction.atomic():
        sub = Subscription.objects.select_for_update(of=("self",)).select_related("product__tax_category").get(pk=sub_id)
        anchor = sub.billing_anchor_day
        for _ in range(MAX_PERIODS_PER_RECORD):
            start = sub.next_billing_date
            if start > until:
                break
            start, end = period_for(start, sub.recurrence, anchor)
            next_start = end + timedelta(days=1)
            if subscription_bills_period(sub, start):
                item, made = BillableItem.objects.get_or_create(
                    subscription=sub,
                    period_start=start,
                    defaults={
                        "tenant_id": sub.tenant_id,
                        "account_id": sub.account_id,
                        "project": single_project(sub),
                        "source": BillableItem.Source.SUBSCRIPTION,
                        "description": f"{sub} ({start:%d/%m/%Y} to {end:%d/%m/%Y})",
                        "qty": 1,
                        "unit_price": price_on(sub, start),
                        "currency": sub.currency,
                        "tax_category": sub.product.tax_category,
                        "period_end": end,
                        "product_id": sub.product_id,
                    },
                )
                if made:
                    created.append(item)
            elif sub.status == Subscription.Status.OVERDUE:
                # Overdue without "keep active": hold the date, so the periods are
                # caught up once the subscription is Active again
                break
            # Suspended, or past the cancellation date: the period is skipped for good
            sub.next_billing_date = next_start
        else:
            raise RuntimeError(f"More than {MAX_PERIODS_PER_RECORD} periods due; check the start date")
        sub.save(update_fields=["next_billing_date", "updated_at"])
    result.items_created += len(created)
    return created


def bill_domain(domain_id, until: date, result: RunResult, fallback_tax: TaxCategory) -> list[BillableItem]:
    created = []
    with transaction.atomic():
        dom = Domain.objects.select_for_update(of=("self",)).select_related("product__tax_category").get(pk=domain_id)
        anchor = (dom.registration_date or dom.next_due_date).day
        for _ in range(MAX_PERIODS_PER_RECORD):
            start = dom.next_due_date
            if start is None or start > until:
                break
            start, end = domain_period_for(start, dom.renewal_years, anchor)
            tax = dom.product.tax_category if dom.product_id else fallback_tax
            item, made = BillableItem.objects.get_or_create(
                domain=dom,
                period_start=start,
                defaults={
                    "tenant_id": dom.tenant_id,
                    "account_id": dom.account_id,
                    "source": BillableItem.Source.DOMAIN,
                    "description": f"Domain renewal {dom.domain_name} ({start:%d/%m/%Y} to {end:%d/%m/%Y})",
                    "qty": 1,
                    "unit_price": dom.price,
                    "currency": dom.currency,
                    "tax_category": tax,
                    "period_end": end,
                    "product_id": dom.product_id,
                },
            )
            if made:
                created.append(item)
            dom.next_due_date = end + timedelta(days=1)
        dom.save(update_fields=["next_due_date", "updated_at"])
    result.items_created += len(created)
    return created


# ---------------------------------------------------------------- drafts


def draft_items_for_account(tenant: Tenant, account_id, items, run_until: date, payment_terms_days: int, result: RunResult):
    """Put a customer's new items on one draft for the period (SUB-02.7)."""
    with transaction.atomic():
        draft = (
            BillingDraft.objects.select_for_update()
            .filter(
                tenant=tenant,
                account_id=account_id,
                status=BillingDraft.Status.DRAFT,
                period_end=run_until,
            )
            .first()
        )
        if draft is None:
            issue = timezone.localdate()
            draft = BillingDraft.objects.create(
                tenant=tenant,
                account_id=account_id,
                number=Sequence.next_number(tenant, Sequence.Kind.DRAFT),
                period_start=min(i.period_start for i in items),
                period_end=run_until,
                issue_date=issue,
                due_date=issue + timedelta(days=payment_terms_days),
                currency=items[0].currency,
            )
            result.drafts_created += 1
        position = draft.lines.count()
        for item in sorted(items, key=lambda i: (i.period_start, i.description)):
            if item.status != BillableItem.Status.UNINVOICED:
                continue
            line = DraftLine(
                draft=draft,
                billable_item=item,
                project=item.project,
                position=position,
                description=item.description,
                qty=item.qty,
                unit_price=item.unit_price,
                tax_category=item.tax_category,
                exemption_code=item.tax_category.exemption_code,
            )
            line.compute()
            line.save()
            position += 1
            item.status = BillableItem.Status.ON_DRAFT
            item.save(update_fields=["status", "updated_at"])
            result.lines_created += 1
        draft.recalculate()
    return draft


# ---------------------------------------------------------------- the run


def run_billing(
    tenant: Tenant,
    until: date | None = None,
    user=None,
    retry_of: ScheduledRun | None = None,
    payment_terms_days: int = DEFAULT_PAYMENT_TERMS_DAYS,
) -> RunResult:
    until = until or timezone.localdate()
    run = ScheduledRun.objects.create(
        tenant=tenant,
        kind=ScheduledRun.Kind.BILLING,
        period_start=until,
        period_end=until,
        retry_of=retry_of,
        retried_by=user if retry_of else None,
    )
    result = RunResult(run=run)
    try:
        _execute(tenant, until, payment_terms_days, result)
    except Exception as exc:  # the run itself failed: record it, never leave it "running"
        result.errors.append({"run": "aborted", "error": str(exc)})
        result.items_created = result.drafts_created = result.lines_created = 0  # rolled back
    run.finished_at = timezone.now()
    run.records_created = result.items_created + result.drafts_created
    run.errors = result.errors
    run.status = ScheduledRun.Status.FAILED if result.errors else ScheduledRun.Status.SUCCEEDED
    run.save(update_fields=["finished_at", "records_created", "errors", "status"])
    return result


def _execute(tenant: Tenant, until: date, payment_terms_days: int, result: RunResult) -> None:
    new_items: dict = defaultdict(list)

    with transaction.atomic():
        Tenant.objects.select_for_update().get(pk=tenant.pk)  # one run at a time per tenant

        sub_ids = Subscription.objects.active().filter(tenant=tenant, next_billing_date__lte=until).exclude(
            status__in=[Subscription.Status.DRAFT, Subscription.Status.EXPIRED]
        ).values_list("pk", flat=True)
        for sub_id in sub_ids:
            try:
                for item in bill_subscription(sub_id, until, result):
                    new_items[item.account_id].append(item)
            except Exception as exc:  # isolate: one bad subscription never blocks the rest
                result.errors.append({"subscription": str(sub_id), "error": str(exc)})

        fallback_tax = TaxCategory.objects.active().filter(tenant=tenant, is_default=True).first()
        dom_ids = Domain.objects.active().filter(
            tenant=tenant,
            auto_renew=True,
            status__in=[Domain.Status.ACTIVE, Domain.Status.GRACE],
            next_due_date__lte=until,
        ).values_list("pk", flat=True)
        for dom_id in dom_ids:
            try:
                if fallback_tax is None:
                    raise ValueError("No default tax category for domains without a product")
                for item in bill_domain(dom_id, until, result, fallback_tax):
                    new_items[item.account_id].append(item)
            except Exception as exc:
                result.errors.append({"domain": str(dom_id), "error": str(exc)})

        # A retry also picks up items an earlier failed run created but never drafted
        leftover = BillableItem.objects.filter(
            tenant=tenant,
            status=BillableItem.Status.UNINVOICED,
            source__in=[BillableItem.Source.SUBSCRIPTION, BillableItem.Source.DOMAIN],
            period_start__lte=until,
        ).exclude(pk__in=[i.pk for items in new_items.values() for i in items])
        for item in leftover:
            new_items[item.account_id].append(item)

        for account_id, items in new_items.items():
            try:
                draft_items_for_account(tenant, account_id, items, until, payment_terms_days, result)
            except Exception as exc:
                result.errors.append({"account": str(account_id), "error": str(exc)})


def retry_run(run: ScheduledRun, user) -> RunResult:
    """Controlled retry by an admin (SUB-02.8). Safe because the run is idempotent."""
    if run.kind != ScheduledRun.Kind.BILLING:
        raise ValueError("Only billing runs can be retried here")
    return run_billing(run.tenant, until=run.period_end, user=user, retry_of=run)
