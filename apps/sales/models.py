from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.common import (
    BaseModel,
    NumberedMixin,
    currency_field,
    money_field,
    positive_money_field,
    unique_number_per_tenant,
)

DISCOUNT_VALIDATORS = [MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))]


class Recurrence(models.TextChoices):
    NONE = "", "One-off"
    MONTHLY = "monthly", "Monthly"
    QUARTERLY = "quarterly", "Quarterly"
    ANNUAL = "annual", "Annual"


class Quote(NumberedMixin, BaseModel):
    """A commercial offer; its content lives in versions (CRM-02.3 to 02.8)."""

    subject = models.CharField(max_length=255)
    account = models.ForeignKey(
        "crm.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="quotes"
    )
    lead = models.ForeignKey(
        "crm.Lead", null=True, blank=True, on_delete=models.PROTECT, related_name="quotes",
        help_text="For prospects not yet an account",
    )
    opportunity = models.ForeignKey(
        "crm.Opportunity", null=True, blank=True, on_delete=models.SET_NULL, related_name="quotes"
    )
    project = models.ForeignKey(
        "projects.Project", null=True, blank=True, on_delete=models.SET_NULL, related_name="quotes"
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="quotes"
    )
    current_version = models.ForeignKey(
        "QuoteVersion", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        constraints = [
            unique_number_per_tenant("quote"),
            models.CheckConstraint(
                condition=models.Q(account__isnull=False) | models.Q(lead__isnull=False),
                name="quote_has_account_or_lead",
            ),
        ]

    def __str__(self):
        return f"{self.number} {self.subject}"


class QuoteVersion(BaseModel):
    """One issued state of a quote; read-only once issued (CRM-02.4)."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ISSUED = "issued", "Issued"
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"
        EXPIRED = "expired", "Expired"

    quote = models.ForeignKey(Quote, on_delete=models.CASCADE, related_name="versions")
    version_no = models.PositiveIntegerField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    issued_at = models.DateTimeField(null=True, blank=True)
    valid_until = models.DateField(null=True, blank=True)
    language = models.CharField(max_length=8, default="el")
    net = money_field()
    tax = money_field()
    gross = money_field()
    currency = currency_field()
    proposal_text = models.TextField(blank=True)
    footer = models.TextField(blank=True)
    private_notes = models.TextField(blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["quote", "-version_no"]
        constraints = [
            models.UniqueConstraint(fields=["quote", "version_no"], name="quoteversion_unique_no")
        ]

    @property
    def is_locked(self) -> bool:
        return self.status != self.Status.DRAFT

    def save(self, *args, **kwargs):
        if self.pk and not self._state.adding:
            previous = QuoteVersion.objects.filter(pk=self.pk).values("status").first()
            if previous and previous["status"] != self.Status.DRAFT:
                changed = set(kwargs.get("update_fields") or []) - {"status", "updated_at", "updated_by"}
                if not kwargs.get("update_fields") or changed:
                    raise ValidationError("An issued quote version is read-only; create a new version.")
        super().save(*args, **kwargs)


class QuoteLine(models.Model):
    version = models.ForeignKey(QuoteVersion, on_delete=models.CASCADE, related_name="lines")
    position = models.PositiveIntegerField(default=0)
    product = models.ForeignKey(
        "catalogue.Product", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    description = models.TextField()
    qty = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("1"))
    unit_price = money_field()
    discount_pct = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("0"), validators=DISCOUNT_VALIDATORS
    )
    tax_category = models.ForeignKey("catalogue.TaxCategory", on_delete=models.PROTECT, related_name="+")
    recurrence = models.CharField(max_length=16, choices=Recurrence.choices, blank=True)
    net = money_field()
    tax = money_field()
    gross = money_field()

    class Meta:
        ordering = ["version", "position"]


class Order(NumberedMixin, BaseModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACTIVE = "active", "Active"
        CANCELLED = "cancelled", "Cancelled"
        FRAUD = "fraud", "Fraud"

    account = models.ForeignKey("crm.Account", on_delete=models.PROTECT, related_name="orders")
    quote_version = models.ForeignKey(
        QuoteVersion, null=True, blank=True, on_delete=models.SET_NULL, related_name="orders"
    )
    date = models.DateField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    currency = currency_field()
    notes = models.TextField(blank=True)

    class Meta:
        constraints = [unique_number_per_tenant("order")]

    def __str__(self):
        return self.number


class OrderLine(models.Model):
    """One-off lines are billed; recurring lines create one subscription each."""

    class LineType(models.TextChoices):
        ONE_OFF = "one_off", "One-off"
        RECURRING = "recurring", "Recurring"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey("catalogue.Product", on_delete=models.PROTECT, related_name="+")
    description = models.TextField(blank=True)
    qty = models.DecimalField(max_digits=12, decimal_places=3, default=Decimal("1"))
    unit_price = positive_money_field()
    discount_pct = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("0"), validators=DISCOUNT_VALIDATORS
    )
    tax_category = models.ForeignKey("catalogue.TaxCategory", on_delete=models.PROTECT, related_name="+")
    line_type = models.CharField(max_length=16, choices=LineType.choices, default=LineType.ONE_OFF)
    recurrence = models.CharField(max_length=16, choices=Recurrence.choices, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(models.Q(line_type="one_off") & models.Q(recurrence=""))
                | (models.Q(line_type="recurring") & ~models.Q(recurrence="")),
                name="orderline_recurrence_matches_type",
            )
        ]
