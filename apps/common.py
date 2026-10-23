"""Shared building blocks for every ERGAVON model.

Ground rules (Data Model v0.1):
- every business table carries tenant_id
- money is Decimal(14,2) with an explicit currency code, never float
- timestamps are stored in UTC (USE_TZ=True)
- created/updated audit columns everywhere
- business records are soft-deleted (archived_at); financial records are
  never deleted, only voided
- UUID primary keys, plus a human-readable number where people quote it
"""

import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

ZERO = Decimal("0.00")


def money_field(**kwargs):
    """Decimal(14,2) for every amount."""
    kwargs.setdefault("max_digits", 14)
    kwargs.setdefault("decimal_places", 2)
    kwargs.setdefault("default", ZERO)
    return models.DecimalField(**kwargs)


def currency_field(**kwargs):
    """ISO 4217 code stored next to every amount. EUR only in v1."""
    kwargs.setdefault("max_length", 3)
    kwargs.setdefault("default", "EUR")
    return models.CharField(**kwargs)


def positive_money_field(**kwargs):
    kwargs.setdefault("validators", [MinValueValidator(ZERO)])
    return money_field(**kwargs)


class UUIDModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        abstract = True


class TenantScopedModel(models.Model):
    tenant = models.ForeignKey(
        "platform.Tenant",
        on_delete=models.PROTECT,
        related_name="+",
        db_index=True,
    )

    class Meta:
        abstract = True


class ActiveQuerySet(models.QuerySet):
    def active(self):
        return self.filter(archived_at__isnull=True)

    def archived(self):
        return self.filter(archived_at__isnull=False)


class SoftDeleteModel(models.Model):
    archived_at = models.DateTimeField(null=True, blank=True, db_index=True)

    objects = ActiveQuerySet.as_manager()

    def archive(self):
        self.archived_at = timezone.now()
        self.save(update_fields=["archived_at", "updated_at"])

    def delete(self, *args, **kwargs):
        # Business records are archived, not deleted. Call hard_delete()
        # explicitly when an admin really means it.
        self.archive()

    def hard_delete(self, *args, **kwargs):
        return super().delete(*args, **kwargs)

    class Meta:
        abstract = True


class BaseModel(UUIDModel, TenantScopedModel, TimeStampedModel, SoftDeleteModel):
    """Default base for business records."""

    class Meta:
        abstract = True


class FinancialModel(UUIDModel, TenantScopedModel, TimeStampedModel):
    """Base for financial records: never deleted, only voided."""

    def delete(self, *args, **kwargs):
        raise ValidationError("Financial records cannot be deleted; void them instead.")

    class Meta:
        abstract = True


class NumberedMixin(models.Model):
    """Human-readable number, unique per tenant (see platform.Sequence)."""

    number = models.CharField(max_length=32, blank=True, db_index=True)

    class Meta:
        abstract = True


def unique_number_per_tenant(model_name):
    return models.UniqueConstraint(
        fields=["tenant", "number"],
        condition=~models.Q(number=""),
        name=f"{model_name}_unique_number_per_tenant",
    )
