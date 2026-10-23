import uuid

from django.contrib.auth.models import AbstractUser, UserManager
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.validators import MaxValueValidator
from django.db import models, transaction

from apps.common import BaseModel, TimeStampedModel, UUIDModel, currency_field


class Tenant(UUIDModel):
    """Root of all data. A single-client install is a database with one tenant."""

    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=64, unique=True)
    base_currency = currency_field()
    default_language = models.CharField(max_length=8, default="en")
    timezone = models.CharField(max_length=64, default="Europe/Athens")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class User(AbstractUser):
    """Staff user. SSO or local password with MFA (NFR-02.2)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        Tenant, null=True, blank=True, on_delete=models.PROTECT, related_name="users"
    )
    email = models.EmailField(unique=True)
    language = models.CharField(max_length=8, default="en")
    timezone = models.CharField(max_length=64, default="Europe/Athens")

    class SSOProvider(models.TextChoices):
        NONE = "", "Local password"
        MICROSOFT = "microsoft", "Microsoft 365"
        GOOGLE = "google", "Google"

    sso_provider = models.CharField(max_length=16, choices=SSOProvider.choices, blank=True)
    mfa_enabled = models.BooleanField(default=False)
    roles = models.ManyToManyField("Role", through="UserRole", related_name="users", blank=True)

    objects = UserManager()

    def has_role(self, name: str) -> bool:
        return self.roles.filter(name=name).exists()


class Role(UUIDModel):
    """Admin, Manager, Salesperson, Operations, Finance (NFR-02.1)."""

    class Name(models.TextChoices):
        ADMIN = "admin", "Admin"
        MANAGER = "manager", "Manager"
        SALESPERSON = "salesperson", "Salesperson"
        OPERATIONS = "operations", "Operations"
        FINANCE = "finance", "Finance"

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="roles")
    name = models.CharField(max_length=32, choices=Name.choices)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "name"], name="role_unique_per_tenant")
        ]

    def __str__(self):
        return self.get_name_display()


class UserRole(UUIDModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    role = models.ForeignKey(Role, on_delete=models.CASCADE)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "role"], name="userrole_unique")
        ]


class AuditLog(UUIDModel):
    """Every create, update, delete, status change, approval and export (NFR-03.1)."""

    class Action(models.TextChoices):
        CREATE = "create", "Create"
        UPDATE = "update", "Update"
        DELETE = "delete", "Delete"
        STATUS = "status", "Status change"
        APPROVE = "approve", "Approval"
        EXPORT = "export", "Export"

    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="+")
    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT)
    object_id = models.CharField(max_length=64)  # UUID or integer key
    record = GenericForeignKey("content_type", "object_id")
    action = models.CharField(max_length=16, choices=Action.choices)
    note = models.CharField(max_length=255, blank=True)
    user = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    at = models.DateTimeField(auto_now_add=True, db_index=True)
    old_values = models.JSONField(default=dict, blank=True)
    new_values = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [models.Index(fields=["content_type", "object_id"])]
        ordering = ["-at"]


MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024


class Attachment(BaseModel):
    """File stored outside the database (NFR-05.3), attached to any record."""

    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT)
    object_id = models.UUIDField()
    owner = GenericForeignKey("content_type", "object_id")
    storage_key = models.CharField(max_length=512)
    filename = models.CharField(max_length=255)
    size = models.PositiveIntegerField(validators=[MaxValueValidator(MAX_ATTACHMENT_BYTES)])
    content_type_header = models.CharField(max_length=128, blank=True)

    class Meta:
        indexes = [models.Index(fields=["content_type", "object_id"])]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(size__lte=MAX_ATTACHMENT_BYTES),
                name="attachment_max_25mb",
            )
        ]


class Sequence(models.Model):
    """Per-tenant numbering for quotes, orders, drafts, tickets and projects."""

    class Kind(models.TextChoices):
        QUOTE = "quote", "Quote"
        ORDER = "order", "Order"
        DRAFT = "draft", "Billing draft"
        TICKET = "ticket", "Ticket"
        PROJECT = "project", "Project"

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE, related_name="sequences")
    kind = models.CharField(max_length=16, choices=Kind.choices)
    prefix = models.CharField(max_length=16, blank=True)
    next_value = models.PositiveIntegerField(default=1)
    padding = models.PositiveSmallIntegerField(default=5)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "kind"], name="sequence_unique_kind")
        ]

    @classmethod
    def next_number(cls, tenant, kind: str) -> str:
        """Allocate the next number atomically (row lock)."""
        with transaction.atomic():
            seq, _ = cls.objects.select_for_update().get_or_create(
                tenant=tenant, kind=kind, defaults={"prefix": kind[:3].upper() + "-"}
            )
            value = seq.next_value
            seq.next_value = value + 1
            seq.save(update_fields=["next_value"])
        return f"{seq.prefix}{value:0{seq.padding}d}"


class CustomField(BaseModel):
    """User-defined fields on core records."""

    class Entity(models.TextChoices):
        ACCOUNT = "account", "Account"
        CONTACT = "contact", "Contact"
        PRODUCT = "product", "Product"
        SUBSCRIPTION = "subscription", "Subscription"
        DOMAIN = "domain", "Domain"
        PROJECT = "project", "Project"
        TICKET = "ticket", "Ticket"

    class FieldType(models.TextChoices):
        TEXT = "text", "Text"
        NUMBER = "number", "Number"
        DATE = "date", "Date"
        DROPDOWN = "dropdown", "Dropdown"
        CHECKBOX = "checkbox", "Checkbox"

    entity = models.CharField(max_length=16, choices=Entity.choices)
    name = models.CharField(max_length=120)
    field_type = models.CharField(max_length=16, choices=FieldType.choices, default=FieldType.TEXT)
    options = models.JSONField(default=list, blank=True, help_text="Choices for dropdown fields")
    required = models.BooleanField(default=False)
    show_on_documents = models.BooleanField(default=False)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["entity", "sort_order", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "entity", "name"], name="customfield_unique_name"
            )
        ]

    def __str__(self):
        return f"{self.get_entity_display()}: {self.name}"


class CustomFieldValue(TimeStampedModel, UUIDModel):
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT, related_name="+")
    field = models.ForeignKey(CustomField, on_delete=models.CASCADE, related_name="values")
    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT)
    object_id = models.UUIDField()
    record = GenericForeignKey("content_type", "object_id")
    value = models.TextField(blank=True)

    class Meta:
        indexes = [models.Index(fields=["content_type", "object_id"])]
        constraints = [
            models.UniqueConstraint(
                fields=["field", "content_type", "object_id"], name="customfieldvalue_unique"
            )
        ]
