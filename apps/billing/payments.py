"""Payments, allocation and customer credit (FIN-01.4 to 01.6).

- Payments are recorded manually, then allocated to one or more drafts;
  partial amounts are allowed on both sides.
- An allocation never exceeds what is left on the payment or what is
  outstanding on the draft.
- Whatever part of a payment is not allocated is the customer's credit,
  available for later drafts (FIN-01.6).
- Payment status (Unpaid, Partially paid, Paid) is recomputed on every
  change and kept separate from subscription status (FIN-01.5).
- A subscription moves to Overdue Payment while one of its drafts is past due
  and unpaid, and back to Active once nothing is overdue.
"""

from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.billing.models import BillingDraft, Payment, PaymentAllocation
from apps.common import ZERO
from apps.platform.audit import acting_as, audit_action
from apps.platform.permissions import FINANCE_ROLES, require_role
from apps.subscriptions.models import Subscription

ALLOCATABLE = (BillingDraft.Status.DRAFT, BillingDraft.Status.APPROVED, BillingDraft.Status.SENT, BillingDraft.Status.INVOICED)
DUE_STATUSES = (BillingDraft.Status.APPROVED, BillingDraft.Status.SENT, BillingDraft.Status.INVOICED)


def record_payment(account, amount, on: date, user, method=Payment.Method.BANK, reference="", notes="", currency="EUR") -> Payment:
    require_role(user, *FINANCE_ROLES)
    amount = Decimal(amount)
    if amount <= ZERO:
        raise ValidationError("A payment amount must be positive.")
    with acting_as(user):
        return Payment.objects.create(
            tenant_id=account.tenant_id,
            account=account,
            amount=amount,
            date=on,
            method=method,
            reference=reference,
            notes=notes,
            currency=currency,
        )


def unallocated(payment: Payment) -> Decimal:
    used = payment.allocations.aggregate(s=Sum("amount"))["s"] or ZERO
    return payment.amount - used


def allocate(payment: Payment, draft: BillingDraft, amount, user) -> PaymentAllocation:
    require_role(user, *FINANCE_ROLES)
    amount = Decimal(amount)
    if amount <= ZERO:
        raise ValidationError("An allocation must be positive.")
    with transaction.atomic(), acting_as(user), audit_action("update", "payment allocated"):
        payment = Payment.objects.select_for_update().get(pk=payment.pk)
        draft = BillingDraft.objects.select_for_update().get(pk=draft.pk)
        if payment.account_id != draft.account_id:
            raise ValidationError("A payment can only be allocated to the same customer's drafts.")
        if payment.currency != draft.currency:
            raise ValidationError("Payment and draft currencies differ.")
        if draft.status not in ALLOCATABLE:
            raise ValidationError(f"Payments cannot be allocated to a {draft.get_status_display()} draft.")
        draft.recalculate(save=False)
        if amount > unallocated(payment):
            raise ValidationError(f"Only {unallocated(payment)} is left on this payment.")
        if amount > draft.outstanding:
            raise ValidationError(f"Only {draft.outstanding} is outstanding on draft {draft.number}.")
        allocation, created = PaymentAllocation.objects.get_or_create(
            payment=payment, draft=draft, defaults={"amount": amount}
        )
        if not created:
            allocation.amount += amount
            allocation.save(update_fields=["amount"])
        draft.recalculate()
        sync_overdue_status(draft.tenant, accounts=[draft.account_id])
    return allocation


def unallocate(allocation: PaymentAllocation, user, amount=None) -> None:
    """Take back all of an allocation, or part of it, into the payment's credit."""
    require_role(user, *FINANCE_ROLES)
    with transaction.atomic(), acting_as(user), audit_action("update", "payment unallocated"):
        allocation = PaymentAllocation.objects.select_for_update().select_related("draft").get(pk=allocation.pk)
        draft = allocation.draft
        if draft.status == BillingDraft.Status.VOIDED:
            raise ValidationError("The draft is voided.")
        take = allocation.amount if amount is None else Decimal(amount)
        if take <= ZERO or take > allocation.amount:
            raise ValidationError(f"Between 0.01 and {allocation.amount} can be unallocated.")
        if take == allocation.amount:
            allocation.delete()
        else:
            allocation.amount -= take
            allocation.save(update_fields=["amount"])
        draft.recalculate()
        sync_overdue_status(draft.tenant, accounts=[draft.account_id])


def open_drafts(account):
    """The customer's drafts with money outstanding, oldest due first."""
    return (
        BillingDraft.objects.filter(account=account, status__in=DUE_STATUSES, outstanding__gt=0)
        .order_by("due_date", "issue_date", "number")
    )


def auto_allocate(payment: Payment, user) -> list[PaymentAllocation]:
    """Spread a payment over the customer's open drafts, oldest due first. The rest stays as credit."""
    made = []
    for draft in open_drafts(payment.account):
        left = unallocated(Payment.objects.get(pk=payment.pk))
        if left <= ZERO:
            break
        made.append(allocate(payment, draft, min(left, draft.outstanding), user))
    return made


def credit_balance(account) -> Decimal:
    """Unallocated money from all the customer's payments (FIN-01.6)."""
    paid = Payment.objects.filter(account=account).aggregate(s=Sum("amount"))["s"] or ZERO
    used = PaymentAllocation.objects.filter(payment__account=account).aggregate(s=Sum("amount"))["s"] or ZERO
    return paid - used


def apply_credit(account, draft: BillingDraft, user) -> list[PaymentAllocation]:
    """Pay a draft from the customer's credit, using the oldest payments first."""
    made = []
    for payment in Payment.objects.filter(account=account).order_by("date", "created_at"):
        draft.refresh_from_db()
        if draft.outstanding <= ZERO:
            break
        left = unallocated(payment)
        if left > ZERO:
            made.append(allocate(payment, draft, min(left, draft.outstanding), user))
    return made


def sync_overdue_status(tenant, today: date | None = None, accounts=None) -> int:
    """Active ⇄ Overdue Payment from past-due, unpaid drafts. Returns how many changed."""
    today = today or timezone.localdate()
    subs = Subscription.objects.active().filter(
        tenant=tenant, status__in=[Subscription.Status.ACTIVE, Subscription.Status.OVERDUE]
    )
    if accounts is not None:
        subs = subs.filter(account_id__in=accounts)
    overdue_ids = set(
        BillingDraft.objects.filter(
            tenant=tenant,
            status__in=DUE_STATUSES,
            outstanding__gt=0,
            due_date__lt=today,
            lines__billable_item__subscription__isnull=False,
        ).values_list("lines__billable_item__subscription_id", flat=True)
    )
    changed = 0
    for sub in subs:
        want = Subscription.Status.OVERDUE if sub.pk in overdue_ids else Subscription.Status.ACTIVE
        if sub.status != want:
            sub.status = want
            sub.save(update_fields=["status", "updated_at"])
            changed += 1
    return changed
