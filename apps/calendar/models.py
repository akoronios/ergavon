from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from apps.common import BaseModel


class Event(BaseModel):
    """Meeting or event on the shared calendar (CAL-01.1).

    The calendar view also reads task deadlines, draft due dates, renewals
    and asset expiries directly from their own tables.
    """

    class Type(models.TextChoices):
        MEETING = "meeting", "Meeting"
        EVENT = "event", "Event"

    title = models.CharField(max_length=255)
    type = models.CharField(max_length=16, choices=Type.choices, default=Type.MEETING)
    start = models.DateTimeField(db_index=True)
    end = models.DateTimeField()
    all_day = models.BooleanField(default=False)
    location = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="events")
    account = models.ForeignKey(
        "crm.Account", null=True, blank=True, on_delete=models.SET_NULL, related_name="events"
    )
    project = models.ForeignKey(
        "projects.Project", null=True, blank=True, on_delete=models.SET_NULL, related_name="events"
    )

    class Meta:
        ordering = ["start"]
        constraints = [
            models.CheckConstraint(condition=models.Q(end__gte=models.F("start")), name="event_end_after_start")
        ]


class Reminder(BaseModel):
    """In-app or email reminder for a dated record (SUB-02.9, AST-01.5, CAL-01.3)."""

    class Kind(models.TextChoices):
        RENEWAL = "renewal", "Renewal"
        WARRANTY = "warranty", "Warranty"
        LICENCE = "licence", "Licence"
        DRAFT_DUE = "draft_due", "Billing draft due"
        TASK = "task", "Task"
        OTHER = "other", "Other"

    class Channel(models.TextChoices):
        IN_APP = "in_app", "In-app"
        EMAIL = "email", "Email"

    kind = models.CharField(max_length=16, choices=Kind.choices)
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.UUIDField()
    target = GenericForeignKey("content_type", "object_id")
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="reminders"
    )
    fire_at = models.DateTimeField(db_index=True)
    lead_days = models.PositiveSmallIntegerField(null=True, blank=True, help_text="e.g. 60, 30, 7")
    channel = models.CharField(max_length=8, choices=Channel.choices, default=Channel.IN_APP)
    message = models.CharField(max_length=255, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["fire_at"]
        indexes = [models.Index(fields=["content_type", "object_id"])]
        constraints = [
            models.UniqueConstraint(
                fields=["content_type", "object_id", "kind", "lead_days", "channel", "recipient"],
                name="reminder_no_duplicates",
                nulls_distinct=False,
            )
        ]
