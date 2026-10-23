from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from apps.common import BaseModel, NumberedMixin, currency_field, money_field, unique_number_per_tenant


class Priority(models.TextChoices):
    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"


class Project(NumberedMixin, BaseModel):
    """Body of work for a client or internal; also the project-management feature (PRJ-01)."""

    class Type(models.TextChoices):
        HOSTING = "hosting", "Hosting"
        SAAS = "saas", "SaaS"
        SOFTWARE = "software", "Software"
        HARDWARE = "hardware", "Hardware"
        SYSTEM = "system", "System"

    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        ACTIVE = "active", "Active"
        ON_HOLD = "on_hold", "On hold"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    name = models.CharField(max_length=255)
    type = models.CharField(max_length=16, choices=Type.choices)
    customer = models.ForeignKey(
        "crm.Account", null=True, blank=True, on_delete=models.PROTECT, related_name="projects",
        help_text="Empty = internal project",
    )
    manager = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="managed_projects"
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PLANNED)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    description = models.TextField(blank=True)
    subscriptions = models.ManyToManyField(
        "subscriptions.Subscription", through="ProjectSubscription", related_name="projects", blank=True
    )
    tickets = models.ManyToManyField(
        "support.Ticket", through="ProjectTicket", related_name="projects", blank=True
    )

    class Meta:
        constraints = [
            unique_number_per_tenant("project"),
            models.CheckConstraint(
                condition=models.Q(end_date__isnull=True)
                | models.Q(start_date__isnull=True)
                | models.Q(end_date__gte=models.F("start_date")),
                name="project_dates_ordered",
            ),
        ]

    @property
    def is_internal(self) -> bool:
        return self.customer_id is None

    def __str__(self):
        return self.name


class ProjectSubscription(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    subscription = models.ForeignKey("subscriptions.Subscription", on_delete=models.CASCADE)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["project", "subscription"], name="projectsubscription_unique")
        ]


class ProjectTicket(models.Model):
    project = models.ForeignKey(Project, on_delete=models.CASCADE)
    ticket = models.ForeignKey("support.Ticket", on_delete=models.CASCADE)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["project", "ticket"], name="projectticket_unique")
        ]


class ProjectLink(models.Model):
    """Generic project link for the other item types; an item may sit in several projects."""

    class TargetType(models.TextChoices):
        DOMAIN = "domain", "Domain"
        PRODUCT = "product", "Product"
        QUOTE = "quote", "Quote"
        DRAFT = "draft", "Billing draft"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="links")
    target_type = models.CharField(max_length=16, choices=TargetType.choices)
    target_id = models.UUIDField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["target_type", "target_id"])]
        constraints = [
            models.UniqueConstraint(fields=["project", "target_type", "target_id"], name="projectlink_unique")
        ]


class Task(BaseModel):
    """Project task or personal to-do (PRJ-01.4, CAL-01.2)."""

    class Status(models.TextChoices):
        NEW = "new", "New"
        IN_PROGRESS = "in_progress", "In progress"
        PENDING = "pending", "Pending"
        COMPLETED = "completed", "Completed"
        POSTPONED = "postponed", "Postponed"

    project = models.ForeignKey(
        Project, null=True, blank=True, on_delete=models.CASCADE, related_name="tasks"
    )
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.CASCADE, related_name="subtasks")
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="tasks"
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.NEW, db_index=True)
    priority = models.CharField(max_length=8, choices=Priority.choices, default=Priority.MEDIUM)
    deadline = models.DateTimeField(null=True, blank=True, db_index=True)
    estimated_effort_minutes = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["deadline", "-priority"]

    def __str__(self):
        return self.title


class TimeEntry(BaseModel):
    """Time on a task or a ticket; approved billable time becomes a BillableItem (PRJ-01.6, 01.7)."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="time_entries")
    task = models.ForeignKey(Task, null=True, blank=True, on_delete=models.PROTECT, related_name="time_entries")
    ticket = models.ForeignKey(
        "support.Ticket", null=True, blank=True, on_delete=models.PROTECT, related_name="time_entries"
    )
    date = models.DateField(db_index=True)
    minutes = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    description = models.TextField(blank=True)
    billable = models.BooleanField(default=True)
    hourly_rate = money_field()
    currency = currency_field()
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    billable_item = models.OneToOneField(
        "billing.BillableItem", null=True, blank=True, on_delete=models.SET_NULL, related_name="time_entry"
    )

    class Meta:
        verbose_name_plural = "time entries"
        constraints = [
            models.CheckConstraint(
                condition=(models.Q(task__isnull=False) & models.Q(ticket__isnull=True))
                | (models.Q(task__isnull=True) & models.Q(ticket__isnull=False)),
                name="timeentry_task_xor_ticket",
            ),
            models.CheckConstraint(
                condition=models.Q(billable_item__isnull=True)
                | (models.Q(billable=True) & models.Q(approved_at__isnull=False)),
                name="timeentry_only_approved_billable_is_billed",
            ),
        ]

    @property
    def hours(self) -> Decimal:
        return (Decimal(self.minutes) / Decimal(60)).quantize(Decimal("0.01"))


class ProjectCost(BaseModel):
    """Direct project cost by period, for margin (PRJ-01.8)."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="costs")
    period_start = models.DateField()
    period_end = models.DateField()
    description = models.CharField(max_length=255)
    amount = money_field()
    currency = currency_field()
    supplier = models.ForeignKey(
        "crm.Account", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
