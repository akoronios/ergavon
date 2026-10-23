from datetime import timedelta

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from apps.common import BaseModel, NumberedMixin, UUIDModel, unique_number_per_tenant
from apps.projects.models import Priority


class Department(BaseModel):
    name = models.CharField(max_length=120)
    email = models.EmailField(blank=True)
    default_assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    is_default = models.BooleanField(default=False)

    def __str__(self):
        return self.name


class SlaPolicy(BaseModel):
    """First-response and resolution targets per priority (TKT-01.3)."""

    priority = models.CharField(max_length=8, choices=Priority.choices)
    first_response_minutes = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    resolution_minutes = models.PositiveIntegerField(validators=[MinValueValidator(1)])

    class Meta:
        verbose_name_plural = "SLA policies"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "priority"],
                condition=models.Q(archived_at__isnull=True),
                name="slapolicy_one_per_priority",
            )
        ]


class Ticket(NumberedMixin, BaseModel):
    """Internal helpdesk ticket (TKT-01)."""

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        IN_PROGRESS = "in_progress", "In progress"
        WAITING = "waiting", "Waiting"
        RESOLVED = "resolved", "Resolved"
        CLOSED = "closed", "Closed"

    subject = models.CharField(max_length=255)
    account = models.ForeignKey("crm.Account", on_delete=models.PROTECT, related_name="tickets")
    contact = models.ForeignKey(
        "crm.Contact", null=True, blank=True, on_delete=models.SET_NULL, related_name="tickets"
    )
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="tickets")
    category = models.CharField(max_length=120, blank=True)
    priority = models.CharField(max_length=8, choices=Priority.choices, default=Priority.MEDIUM)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN, db_index=True)
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="assigned_tickets"
    )
    sla_policy = models.ForeignKey(SlaPolicy, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    first_response_at = models.DateTimeField(null=True, blank=True)
    response_due = models.DateTimeField(null=True, blank=True, db_index=True)
    resolution_due = models.DateTimeField(null=True, blank=True, db_index=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    prevent_client_closure = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]
        constraints = [unique_number_per_tenant("ticket")]

    def __str__(self):
        return f"#{self.number} {self.subject}"

    def apply_sla(self):
        """Set due times from the policy for this priority."""
        policy = SlaPolicy.objects.active().filter(tenant_id=self.tenant_id, priority=self.priority).first()
        self.sla_policy = policy
        if policy and self.created_at:
            self.response_due = self.created_at + timedelta(minutes=policy.first_response_minutes)
            self.resolution_due = self.created_at + timedelta(minutes=policy.resolution_minutes)


class TicketMessage(BaseModel):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="messages")
    author_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    author_contact = models.ForeignKey(
        "crm.Contact", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    body = models.TextField()
    is_internal_note = models.BooleanField(default=False)

    class Meta:
        ordering = ["created_at"]


class TicketStatusChange(UUIDModel):
    """Every status change, so reopenings are recorded and counted (TKT-01.2)."""

    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="status_changes")
    from_status = models.CharField(max_length=16, choices=Ticket.Status.choices, blank=True)
    to_status = models.CharField(max_length=16, choices=Ticket.Status.choices)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["changed_at"]

    @property
    def is_reopen(self) -> bool:
        return self.from_status in ("resolved", "closed") and self.to_status not in ("resolved", "closed")


class PredefinedReply(BaseModel):
    title = models.CharField(max_length=255)
    body = models.TextField()
    department = models.ForeignKey(
        Department, null=True, blank=True, on_delete=models.SET_NULL, related_name="predefined_replies"
    )

    class Meta:
        verbose_name_plural = "predefined replies"

    def __str__(self):
        return self.title
