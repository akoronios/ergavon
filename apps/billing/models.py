from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Sum

from apps.common import (
    ZERO,
    FinancialModel,
    NumberedMixin,
    TenantScopedModel,
    UUIDModel,
    currency_field,
    money_field,
    unique_number_per_tenant,
)

CENT = Decimal("0.01")


def round_money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


class BillableItem(FinancialModel):
    """Every charge starts here, whatever its source (rule 1)."""

    class Source(models.TextChoices):
        ORDER_LINE = "order_line", "One-off order line"
        TIME = "time", "Approved time"
        PRODUCT = "product", "Product added directly"
        SUBSCRIPTION = "subscription", "Subscription period"
        DOMAIN = "domain", "Domain renewal"

    class Status(models.TextChoices):
        UNINVOICED = "uninvoiced", "Uninvoiced"
        ON_DRAFT = "on_draft", "On draft"
        BILLED = "billed", "Billed"

    account = models.ForeignKey("crm.Account", on_delete=models.PROTECT, related_name="billable_items")
    project = models.ForeignKey(
        "projects.Project", null=True, blank=True, on_delete=models.SET_NULL, related_name="billable_items"
    )
    source = models.CharField(max_length=16, choices=Source.choices)
    description = models.TextField()
    qty = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("1"))
    unit_price = money_field()
    currency = currency_field()
    tax_category = models.ForeignKey("catalogue.TaxCategory", on_delete=models.PROTECT, related_name="+")
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.UNINVOICED, db_index=True)
    subscription = models.ForeignKey(
        "subscriptions.Subscription", null=True, blank=True, on_delete=models.PROTECT, related_name="billable_items"
    )
    domain = models.ForeignKey(
        "subscriptions.Domain", null=True, blank=True, on_delete=models.PROTECT, related_name="billable_items"
    )
    order_line = models.ForeignKey(
        "sales.OrderLine", null=True, blank=True, on_delete=models.PROTECT, related_name="billable_items"
    )
    product = models.ForeignKey(
        "catalogue.Product", null=True, blank=True, on_delete=models.PROTECT, related_name="billable_items"
    )

    class Meta:
        constraints = [
            # Rule 3: one item per subscription period and per domain renewal period
            models.UniqueConstraint(
                fields=["subscription", "period_start"],
                condition=models.Q(subscription__isnull=False),
                name="billableitem_one_per_subscription_period",
            ),
            models.UniqueConstraint(
                fields=["domain", "period_start"],
                condition=models.Q(domain__isnull=False),
                name="billableitem_one_per_domain_period",
            ),
            models.CheckConstraint(
                condition=~models.Q(source="subscription")
                | (models.Q(subscription__isnull=False) & models.Q(period_start__isnull=False)),
                name="billableitem_subscription_has_period",
            ),
            models.CheckConstraint(
                condition=~models.Q(source="domain")
                | (models.Q(domain__isnull=False) & models.Q(period_start__isnull=False)),
                name="billableitem_domain_has_period",
            ),
            models.CheckConstraint(
                condition=~models.Q(source="order_line") | models.Q(order_line__isnull=False),
                name="billableitem_order_line_linked",
            ),
        ]

    @property
    def net(self) -> Decimal:
        return round_money(self.qty * self.unit_price)


class BillingDraft(NumberedMixin, FinancialModel):
    """Non-fiscal charge proposal sent to accounting; not a tax invoice (FIN-01)."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        APPROVED = "approved", "Approved"
        SENT = "sent", "Sent to accounting"
        INVOICED = "invoiced", "Invoiced"
        VOIDED = "voided", "Voided"

    class PaymentStatus(models.TextChoices):
        UNPAID = "unpaid", "Unpaid"
        PARTIAL = "partial", "Partially paid"
        PAID = "paid", "Paid"

    account = models.ForeignKey("crm.Account", on_delete=models.PROTECT, related_name="billing_drafts")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT, db_index=True)
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    issue_date = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True, db_index=True)
    language = models.CharField(max_length=8, default="el")
    net = money_field()
    tax = money_field()
    gross = money_field()
    currency = currency_field()
    paid = money_field()
    outstanding = money_field()
    payment_status = models.CharField(max_length=16, choices=PaymentStatus.choices, default=PaymentStatus.UNPAID)
    external_invoice_no = models.CharField(max_length=64, blank=True)
    external_invoice_date = models.DateField(null=True, blank=True)
    reissued_from = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="reissues"
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        constraints = [
            unique_number_per_tenant("billingdraft"),
            # Rule 9: an invoiced draft carries the external invoice number and date
            models.CheckConstraint(
                condition=~models.Q(status="invoiced")
                | (~models.Q(external_invoice_no="") & models.Q(external_invoice_date__isnull=False)),
                name="billingdraft_invoiced_has_external_ref",
            ),
        ]

    def __str__(self):
        return self.number or str(self.pk)

    # Once a draft leaves Draft its lines and totals are frozen; once Invoiced or
    # Voided only the payment figures may still change (rule 9, FIN-01.3).
    LOCKED_STATUSES = ("invoiced", "voided")
    PAYMENT_FIELDS = {"paid", "outstanding", "payment_status", "updated_at", "updated_by"}

    def save(self, *args, **kwargs):
        if self.pk and not self._state.adding:
            stored = BillingDraft.objects.filter(pk=self.pk).values_list("status", flat=True).first()
            if stored in self.LOCKED_STATUSES:
                fields = kwargs.get("update_fields")
                if fields is None or set(fields) - self.PAYMENT_FIELDS:
                    raise ValidationError(f"A {stored} billing draft is read-only.")
        super().save(*args, **kwargs)

    def recalculate(self, save=True):
        """Totals from lines (only while Draft) and payment figures from allocations."""
        fields = ["paid", "outstanding", "payment_status", "updated_at"]
        if self.status == self.Status.DRAFT:
            totals = self.lines.aggregate(net=Sum("net"), tax=Sum("tax"), gross=Sum("gross"))
            self.net = totals["net"] or ZERO
            self.tax = totals["tax"] or ZERO
            self.gross = totals["gross"] or ZERO
            fields += ["net", "tax", "gross"]
        self.paid = self.allocations.aggregate(s=Sum("amount"))["s"] or ZERO
        self.outstanding = self.gross - self.paid
        if self.paid <= ZERO:
            self.payment_status = self.PaymentStatus.UNPAID
        elif self.outstanding > ZERO:
            self.payment_status = self.PaymentStatus.PARTIAL
        else:
            self.payment_status = self.PaymentStatus.PAID
        if save:
            self.save(update_fields=fields)


class DraftLine(UUIDModel):
    """A line always points at a BillableItem; the project tag is optional (rules 1, 8)."""

    draft = models.ForeignKey(BillingDraft, on_delete=models.CASCADE, related_name="lines")
    billable_item = models.ForeignKey(BillableItem, on_delete=models.PROTECT, related_name="draft_lines")
    project = models.ForeignKey(
        "projects.Project", null=True, blank=True, on_delete=models.SET_NULL, related_name="draft_lines"
    )
    position = models.PositiveIntegerField(default=0)
    description = models.TextField()
    qty = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("1"))
    unit_price = money_field()
    tax_category = models.ForeignKey("catalogue.TaxCategory", on_delete=models.PROTECT, related_name="+")
    exemption_code = models.CharField(max_length=32, blank=True)
    net = money_field()
    tax = money_field()
    gross = money_field()

    class Meta:
        ordering = ["draft", "position"]
        constraints = [
            # Rule 8: a shared item may be split across projects, one line per project
            models.UniqueConstraint(
                fields=["draft", "billable_item", "project"],
                name="draftline_one_per_item_project",
                nulls_distinct=False,
            )
        ]

    def _check_editable(self):
        status = BillingDraft.objects.filter(pk=self.draft_id).values_list("status", flat=True).first()
        if status != BillingDraft.Status.DRAFT:
            raise ValidationError("Lines can only change while the billing draft is in Draft.")

    def save(self, *args, **kwargs):
        self._check_editable()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self._check_editable()
        return super().delete(*args, **kwargs)

    def compute(self):
        self.net = round_money(self.qty * self.unit_price)
        self.tax = round_money(self.net * self.tax_category.rate / Decimal(100))
        self.gross = self.net + self.tax


class Payment(FinancialModel):
    """Recorded manually and allocated to drafts, partial allowed (FIN-01.4)."""

    class Method(models.TextChoices):
        BANK = "bank", "Bank deposit"
        CARD = "card", "Credit / debit card"
        DEFERRED = "deferred", "Deferred payment"
        CHEQUE = "cheque", "Cheque"
        CASH = "cash", "Cash"

    account = models.ForeignKey("crm.Account", on_delete=models.PROTECT, related_name="payments")
    date = models.DateField()
    amount = money_field()
    currency = currency_field()
    method = models.CharField(max_length=16, choices=Method.choices, default=Method.BANK)
    reference = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    drafts = models.ManyToManyField(BillingDraft, through="PaymentAllocation", related_name="payments")

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="payment_amount_positive")
        ]

    @property
    def unallocated(self) -> Decimal:
        return self.amount - (self.allocations.aggregate(s=Sum("amount"))["s"] or ZERO)


class PaymentAllocation(UUIDModel):
    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="allocations")
    draft = models.ForeignKey(BillingDraft, on_delete=models.PROTECT, related_name="allocations")
    amount = money_field()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="allocation_amount_positive"),
            models.UniqueConstraint(fields=["payment", "draft"], name="allocation_unique"),
        ]


class CreditEntry(FinancialModel):
    """Customer credit from unallocated or overpaid amounts (FIN-01.6). Positive adds, negative uses."""

    account = models.ForeignKey("crm.Account", on_delete=models.PROTECT, related_name="credit_entries")
    amount = money_field()
    currency = currency_field()
    reason = models.CharField(max_length=255)
    payment = models.ForeignKey(Payment, null=True, blank=True, on_delete=models.PROTECT, related_name="credits")
    allocation = models.ForeignKey(
        PaymentAllocation, null=True, blank=True, on_delete=models.PROTECT, related_name="credits"
    )

    class Meta:
        verbose_name_plural = "credit entries"


class AccountingExport(UUIDModel, TenantScopedModel):
    """CSV export for the accountant, logged (INT-03)."""

    file_key = models.CharField(max_length=512)
    exported_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    exported_at = models.DateTimeField(auto_now_add=True)
    row_count = models.PositiveIntegerField(default=0)
    mapping_version = models.CharField(max_length=32, blank=True)

    class Meta:
        ordering = ["-exported_at"]


class ExportedRecord(UUIDModel, TenantScopedModel):
    """Flags a draft or payment as exported, so it is never posted twice."""

    class RecordType(models.TextChoices):
        DRAFT = "draft", "Billing draft"
        PAYMENT = "payment", "Payment"

    export = models.ForeignKey(AccountingExport, on_delete=models.PROTECT, related_name="records")
    record_type = models.CharField(max_length=16, choices=RecordType.choices)
    record_id = models.UUIDField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "record_type", "record_id"], name="exportedrecord_once"
            )
        ]
