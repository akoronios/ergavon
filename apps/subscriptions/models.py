from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.common import BaseModel, UUIDModel, TenantScopedModel, currency_field, money_field, positive_money_field
from apps.sales.models import Recurrence


class Subscription(BaseModel):
    """Recurring agreement: one billed account, one service (SUB-01, SUB-02)."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        SUSPENDED = "suspended", "Suspended"
        CANCELLED = "cancelled", "Cancelled"
        EXPIRED = "expired", "Expired"
        OVERDUE = "overdue_payment", "Overdue Payment"

    account = models.ForeignKey("crm.Account", on_delete=models.PROTECT, related_name="subscriptions")
    product = models.ForeignKey("catalogue.Product", on_delete=models.PROTECT, related_name="subscriptions")
    origin_order_line = models.OneToOneField(
        "sales.OrderLine", null=True, blank=True, on_delete=models.SET_NULL, related_name="subscription"
    )
    description = models.CharField(max_length=255, blank=True)
    agreed_price = positive_money_field()
    currency = currency_field()
    start_date = models.DateField()
    billing_anchor_day = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(31)]
    )
    recurrence = models.CharField(
        max_length=16, choices=[c for c in Recurrence.choices if c[0]], default=Recurrence.MONTHLY
    )
    next_billing_date = models.DateField(db_index=True)
    renewal_term_months = models.PositiveSmallIntegerField(
        default=12, help_text="Contract renewal term; separate from billing recurrence (SUB-01.2)"
    )
    renewal_date = models.DateField(null=True, blank=True, db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True)
    cancel_effective_date = models.DateField(null=True, blank=True)
    keep_active_when_overdue = models.BooleanField(default=False)
    supplier_cost = money_field()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(billing_anchor_day__gte=1, billing_anchor_day__lte=31),
                name="subscription_anchor_day_range",
            ),
            models.CheckConstraint(
                condition=~models.Q(status="cancelled") | models.Q(cancel_effective_date__isnull=False),
                name="subscription_cancel_needs_date",
            ),
        ]

    def __str__(self):
        return self.description or f"{self.product} for {self.account}"

    @property
    def is_billable(self) -> bool:
        """Suspended subscriptions create no drafts (SUB-02.2, proposed)."""
        if self.status in (self.Status.ACTIVE,):
            return True
        return self.status == self.Status.OVERDUE and self.keep_active_when_overdue


class SubscriptionPrice(UUIDModel):
    """Price history; a change applies from the next period (SUB-01.5)."""

    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, related_name="prices")
    price = positive_money_field()
    currency = currency_field()
    valid_from = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["subscription", "-valid_from"]
        constraints = [
            models.UniqueConstraint(fields=["subscription", "valid_from"], name="subscriptionprice_unique_from")
        ]


class Domain(BaseModel):
    """A registered domain, its own entity as in WHMCS. Renewals become billable items."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACTIVE = "active", "Active"
        GRACE = "grace", "Grace Period"
        EXPIRED = "expired", "Expired"
        TRANSFERRED_AWAY = "transferred_away", "Transferred Away"
        CANCELLED = "cancelled", "Cancelled"

    account = models.ForeignKey("crm.Account", on_delete=models.PROTECT, related_name="domains")
    product = models.ForeignKey(
        "catalogue.Product", null=True, blank=True, on_delete=models.PROTECT, related_name="domains"
    )
    domain_name = models.CharField(max_length=253)
    registrar = models.CharField(max_length=120, blank=True)
    registration_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True, db_index=True)
    next_due_date = models.DateField(null=True, blank=True, db_index=True)
    renewal_years = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1), MaxValueValidator(10)])
    price = positive_money_field()
    registrar_cost = money_field()
    currency = currency_field()
    auto_renew = models.BooleanField(default=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    class Meta:
        ordering = ["domain_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "domain_name"],
                condition=models.Q(archived_at__isnull=True),
                name="domain_unique_active_name",
            )
        ]

    def save(self, *args, **kwargs):
        self.domain_name = (self.domain_name or "").strip().lower()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.domain_name


class ScheduledRun(UUIDModel, TenantScopedModel):
    """History of billing and reminder runs, with admin retry (SUB-02.8)."""

    class Kind(models.TextChoices):
        BILLING = "billing", "Billing"
        REMINDERS = "reminders", "Reminders"

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    kind = models.CharField(max_length=16, choices=Kind.choices)
    period_start = models.DateField()
    period_end = models.DateField()
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RUNNING)
    records_created = models.PositiveIntegerField(default=0)
    errors = models.JSONField(default=list, blank=True)
    retry_of = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="retries")
    retried_by = models.ForeignKey(
        "platform.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["-started_at"]
