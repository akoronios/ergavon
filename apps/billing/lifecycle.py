"""Billing draft lifecycle (FIN-01.1 to 01.3).

    Draft ──approve──▶ Approved ──send──▶ Sent to accounting ──invoice──▶ Invoiced
      ▲                   │                     │
      └──── reopen ───────┘                     │
    Draft / Approved / Sent ──void──▶ Voided ──reissue──▶ new Draft

- Approve, send, invoice, void, reopen and reissue need the Finance or Admin role.
- Invoiced stores the external invoice number and date and locks the draft;
  only payments can still be allocated to it.
- Void is refused while payments are allocated (unallocate first); it returns
  the draft's billable items to Uninvoiced so they can be billed again.
"""

from datetime import date

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.billing.models import BillableItem, BillingDraft, DraftLine
from apps.platform.audit import acting_as, audit_action
from apps.platform.models import Sequence
from apps.platform.permissions import FINANCE_ROLES, require_role

S = BillingDraft.Status


def _locked(draft: BillingDraft) -> BillingDraft:
    return BillingDraft.objects.select_for_update().get(pk=draft.pk)


def _expect(draft: BillingDraft, *statuses: str) -> None:
    if draft.status not in statuses:
        allowed = ", ".join(BillingDraft.Status(s).label for s in statuses)
        raise ValidationError(
            f"Draft {draft.number} is {draft.get_status_display()}; this needs it to be {allowed}."
        )


def _set_items(draft: BillingDraft, status: str) -> None:
    BillableItem.objects.filter(draft_lines__draft=draft).update(status=status, updated_at=timezone.now())


def approve(draft: BillingDraft, user) -> BillingDraft:
    require_role(user, *FINANCE_ROLES)
    with transaction.atomic(), acting_as(user), audit_action("approve", "draft approved"):
        draft = _locked(draft)
        _expect(draft, S.DRAFT)
        if not draft.lines.exists():
            raise ValidationError("An empty billing draft cannot be approved.")
        draft.recalculate(save=False)  # totals frozen from here on
        draft.status = S.APPROVED
        draft.approved_by = user
        draft.approved_at = timezone.now()
        draft.save()
    return draft


def reopen(draft: BillingDraft, user) -> BillingDraft:
    """Take an approved draft back to Draft to correct it before it is sent."""
    require_role(user, *FINANCE_ROLES)
    with transaction.atomic(), acting_as(user):
        draft = _locked(draft)
        _expect(draft, S.APPROVED)
        draft.status = S.DRAFT
        draft.approved_by = None
        draft.approved_at = None
        draft.save()
    return draft


def send_to_accounting(draft: BillingDraft, user) -> BillingDraft:
    require_role(user, *FINANCE_ROLES)
    with transaction.atomic(), acting_as(user):
        draft = _locked(draft)
        _expect(draft, S.APPROVED)
        draft.status = S.SENT
        draft.save()
    return draft


def mark_invoiced(draft: BillingDraft, user, external_invoice_no: str, external_invoice_date: date) -> BillingDraft:
    require_role(user, *FINANCE_ROLES)
    if not (external_invoice_no or "").strip() or not external_invoice_date:
        raise ValidationError("The external invoice number and date are both required.")
    with transaction.atomic(), acting_as(user):
        draft = _locked(draft)
        _expect(draft, S.SENT)
        draft.status = S.INVOICED
        draft.external_invoice_no = external_invoice_no.strip()
        draft.external_invoice_date = external_invoice_date
        draft.save()
        _set_items(draft, BillableItem.Status.BILLED)
    return draft


def void(draft: BillingDraft, user, reason: str) -> BillingDraft:
    require_role(user, *FINANCE_ROLES)
    if not (reason or "").strip():
        raise ValidationError("A reason is required to void a billing draft.")
    with transaction.atomic(), acting_as(user), audit_action("status", f"voided: {reason.strip()}"):
        draft = _locked(draft)
        _expect(draft, S.DRAFT, S.APPROVED, S.SENT)
        if draft.allocations.exists():
            raise ValidationError("Payments are allocated to this draft; unallocate them before voiding.")
        draft.status = S.VOIDED
        draft.notes = (draft.notes + "\n" if draft.notes else "") + f"Voided: {reason.strip()}"
        draft.save()
        _set_items(draft, BillableItem.Status.UNINVOICED)
    return draft


def reissue(draft: BillingDraft, user) -> BillingDraft:
    """A new Draft carrying the voided draft's items that are still unbilled."""
    require_role(user, *FINANCE_ROLES)
    with transaction.atomic(), acting_as(user):
        draft = _locked(draft)
        _expect(draft, S.VOIDED)
        lines = list(
            draft.lines.select_related("billable_item", "tax_category").filter(
                billable_item__status=BillableItem.Status.UNINVOICED
            )
        )
        if not lines:
            raise ValidationError("Nothing left to reissue: the items are already on another draft.")
        today = timezone.localdate()
        terms = (draft.due_date - draft.issue_date) if draft.due_date and draft.issue_date else None
        new = BillingDraft.objects.create(
            tenant_id=draft.tenant_id,
            account_id=draft.account_id,
            number=Sequence.next_number(draft.tenant, Sequence.Kind.DRAFT),
            period_start=draft.period_start,
            period_end=draft.period_end,
            issue_date=today,
            due_date=today + terms if terms else None,
            language=draft.language,
            currency=draft.currency,
            reissued_from=draft,
        )
        for old in lines:
            line = DraftLine(
                draft=new,
                billable_item=old.billable_item,
                project=old.project,
                position=old.position,
                description=old.description,
                qty=old.qty,
                unit_price=old.unit_price,
                tax_category=old.tax_category,
                exemption_code=old.exemption_code,
            )
            line.compute()
            line.save()
        BillableItem.objects.filter(pk__in=[ln.billable_item_id for ln in lines]).update(
            status=BillableItem.Status.ON_DRAFT
        )
        new.recalculate()
    return new
