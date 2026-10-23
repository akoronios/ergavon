from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.common import BaseModel, currency_field, money_field
from apps.validators import validate_greek_vat


class Account(BaseModel):
    """A legal entity with one or more roles (CRM-01.1, 01.2)."""

    legal_name = models.CharField(max_length=255, db_index=True)
    trading_name = models.CharField(max_length=255, blank=True)
    tax_id = models.CharField("ΑΦΜ / VAT number", max_length=20, blank=True, db_index=True)
    tax_office = models.CharField("ΔΟΥ", max_length=120, blank=True)
    gemi_number = models.CharField("ΓΕΜΗ number", max_length=20, blank=True)
    business_activity = models.CharField("Business activity / Επάγγελμα", max_length=255, blank=True)
    country = models.CharField(max_length=2, default="GR")
    address_line1 = models.CharField(max_length=255, blank=True)
    address_line2 = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=120, blank=True)
    region = models.CharField(max_length=120, blank=True)
    postcode = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    website = models.URLField(blank=True)

    class ViesStatus(models.TextChoices):
        NOT_CHECKED = "", "Not checked"
        VALID = "valid", "Valid"
        INVALID = "invalid", "Invalid"
        ERROR = "error", "Service error"

    vies_status = models.CharField(max_length=10, choices=ViesStatus.choices, blank=True)
    vies_checked_at = models.DateTimeField(null=True, blank=True)
    language = models.CharField(max_length=8, default="el")
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="owned_accounts",
    )
    # Search helper: lower-case, accent-free legal + trading name (CRM-01.6)
    search_name = models.CharField(max_length=520, blank=True, db_index=True, editable=False)

    class Meta:
        ordering = ["legal_name"]

    def __str__(self):
        return self.trading_name or self.legal_name

    def clean(self):
        if self.country == "GR" and self.tax_id:
            validate_greek_vat(self.tax_id)

    def save(self, *args, **kwargs):
        from apps.crm.search import normalise

        self.search_name = normalise(f"{self.legal_name} {self.trading_name}")
        super().save(*args, **kwargs)


class AccountRole(models.Model):
    class Role(models.TextChoices):
        CLIENT = "client", "Client"
        PROVIDER = "provider", "Provider"
        AGENT = "agent", "Agent"
        PARTNER = "partner", "Partner"

    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="roles")
    role = models.CharField(max_length=16, choices=Role.choices)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["account", "role"], name="accountrole_unique")
        ]


class Contact(BaseModel):
    """A person, linked to accounts through memberships (CRM-01.3)."""

    first_name = models.CharField(max_length=120)
    last_name = models.CharField(max_length=120, blank=True)
    email = models.EmailField(blank=True, db_index=True)
    phone = models.CharField(max_length=40, blank=True)
    mobile = models.CharField(max_length=40, blank=True)
    language = models.CharField(max_length=8, default="el")
    marketing_consent = models.BooleanField(default=False)
    consent_date = models.DateTimeField(null=True, blank=True)
    consent_source = models.CharField(max_length=120, blank=True)
    accounts = models.ManyToManyField(Account, through="Membership", related_name="contacts")
    anonymised_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["last_name", "first_name"]

    def __str__(self):
        return f"{self.first_name} {self.last_name}".strip()

    def anonymise(self):
        """GDPR erasure: wipe personal fields, keep commercial records (NFR-04.2)."""
        from django.utils import timezone

        self.first_name = "Erased"
        self.last_name = ""
        self.email = ""
        self.phone = ""
        self.mobile = ""
        self.consent_source = ""
        self.marketing_consent = False
        self.anonymised_at = timezone.now()
        self.save()


class Membership(models.Model):
    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name="memberships")
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=120, blank=True, help_text="e.g. Billing, Technical, CEO")
    is_primary = models.BooleanField(default=False)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_date__isnull=True)
                | models.Q(start_date__isnull=True)
                | models.Q(end_date__gte=models.F("start_date")),
                name="membership_dates_ordered",
            )
        ]


class Lead(BaseModel):
    """An unqualified prospect; a pipeline stage, not an account role (CRM-02.1)."""

    class Status(models.TextChoices):
        NEW = "new", "New"
        CONTACTED = "contacted", "Contacted"
        QUALIFIED = "qualified", "Qualified"
        CONVERTED = "converted", "Converted"
        DISQUALIFIED = "disqualified", "Disqualified"

    company_name = models.CharField(max_length=255, blank=True)
    first_name = models.CharField(max_length=120, blank=True)
    last_name = models.CharField(max_length=120, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    source = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.NEW)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="leads"
    )
    converted_to = models.OneToOneField(
        "Opportunity", null=True, blank=True, on_delete=models.SET_NULL, related_name="from_lead"
    )

    def __str__(self):
        return self.company_name or f"{self.first_name} {self.last_name}".strip()


class Opportunity(BaseModel):
    """A qualified potential sale (CRM-02.2)."""

    class Stage(models.TextChoices):
        QUALIFICATION = "qualification", "Qualification"
        PROPOSAL = "proposal", "Proposal"
        NEGOTIATION = "negotiation", "Negotiation"
        WON = "won", "Won"
        LOST = "lost", "Lost"

    name = models.CharField(max_length=255)
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="opportunities")
    contact = models.ForeignKey(
        Contact, null=True, blank=True, on_delete=models.SET_NULL, related_name="opportunities"
    )
    value = money_field()
    currency = currency_field()
    probability = models.PositiveSmallIntegerField(
        default=10, validators=[MinValueValidator(0), MaxValueValidator(100)]
    )
    stage = models.CharField(max_length=16, choices=Stage.choices, default=Stage.QUALIFICATION)
    expected_close = models.DateField(null=True, blank=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="opportunities"
    )
    lost_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name_plural = "opportunities"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(probability__gte=0, probability__lte=100),
                name="opportunity_probability_range",
            )
        ]

    def clean(self):
        if self.stage == self.Stage.LOST and not self.lost_reason:
            raise ValidationError({"lost_reason": "A lost opportunity needs a lost reason."})

    def __str__(self):
        return self.name


class Activity(BaseModel):
    """One logged interaction; one record = one activity in every report (CRM-03)."""

    class Type(models.TextChoices):
        CALL = "call", "Call"
        EMAIL = "email", "Email"
        MEETING = "meeting", "Meeting"
        FOLLOW_UP = "follow_up", "Follow-up"

    type = models.CharField(max_length=16, choices=Type.choices)
    subject = models.CharField(max_length=255)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="activities"
    )
    at = models.DateTimeField(db_index=True)
    outcome = models.TextField(blank=True)
    next_action = models.CharField(max_length=255, blank=True)
    next_due = models.DateField(null=True, blank=True)
    account = models.ForeignKey(
        Account, null=True, blank=True, on_delete=models.CASCADE, related_name="activities"
    )
    contact = models.ForeignKey(
        Contact, null=True, blank=True, on_delete=models.CASCADE, related_name="activities"
    )
    opportunity = models.ForeignKey(
        Opportunity, null=True, blank=True, on_delete=models.CASCADE, related_name="activities"
    )
    ticket = models.ForeignKey(
        "support.Ticket", null=True, blank=True, on_delete=models.CASCADE, related_name="activities"
    )
    task = models.ForeignKey(
        "projects.Task", null=True, blank=True, on_delete=models.CASCADE, related_name="activities"
    )
    payment = models.ForeignKey(
        "billing.Payment", null=True, blank=True, on_delete=models.CASCADE, related_name="activities"
    )

    class Meta:
        verbose_name_plural = "activities"
        ordering = ["-at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(account__isnull=False)
                | models.Q(contact__isnull=False)
                | models.Q(opportunity__isnull=False)
                | models.Q(ticket__isnull=False)
                | models.Q(task__isnull=False)
                | models.Q(payment__isnull=False),
                name="activity_linked_to_something",
            )
        ]
