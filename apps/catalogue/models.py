from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models

from apps.common import BaseModel, currency_field, money_field, positive_money_field


class TaxCategory(BaseModel):
    """VAT category, preloaded with Greek rates (GR-01.5 to 01.7)."""

    class Kind(models.TextChoices):
        STANDARD = "standard", "Standard"
        REDUCED = "reduced", "Reduced"
        SUPER_REDUCED = "super_reduced", "Super-reduced"
        ISLAND = "island", "Island reduced"
        EXEMPT = "exempt", "VAT exempt"
        REVERSE_CHARGE = "reverse_charge", "EU reverse charge"

    name = models.CharField(max_length=120)
    rate = models.DecimalField(
        max_digits=5, decimal_places=2, validators=[MinValueValidator(Decimal("0"))],
        help_text="Percent, e.g. 24.00",
    )
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.STANDARD)
    exemption_code = models.CharField(max_length=32, blank=True)
    mydata_category = models.CharField(
        max_length=32, blank=True, help_text="myDATA VAT/exemption category, for the post-beta myDATA link"
    )
    is_default = models.BooleanField(default=False)

    class Meta:
        verbose_name_plural = "tax categories"
        constraints = [
            models.CheckConstraint(condition=models.Q(rate__gte=0), name="taxcategory_rate_positive"),
            models.CheckConstraint(
                condition=~models.Q(kind="exempt") | ~models.Q(exemption_code=""),
                name="taxcategory_exempt_needs_code",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.rate}%)"


class Product(BaseModel):
    """Catalogue item (AST-01.1 to 01.3)."""

    class Type(models.TextChoices):
        SOFTWARE = "software", "Software"
        HARDWARE = "hardware", "Hardware"
        DOMAIN = "domain", "Domain"
        SAAS = "saas", "SaaS"
        HAAS = "haas", "HaaS"
        SERVICE = "service", "Service"

    class ChargingUnit(models.TextChoices):
        ONE_OFF = "one_off", "One-off"
        HOUR = "hour", "Hour"
        DAY = "day", "Day"
        WEEK = "week", "Week"
        MONTH = "month", "Month"

    code = models.CharField(max_length=64)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    type = models.CharField(max_length=16, choices=Type.choices)
    charging_unit = models.CharField(
        max_length=16, choices=ChargingUnit.choices, default=ChargingUnit.ONE_OFF,
        help_text="How the product is priced; separate from subscription recurrence",
    )
    tax_category = models.ForeignKey(TaxCategory, on_delete=models.PROTECT, related_name="products")
    default_price = positive_money_field()
    purchase_cost = positive_money_field()
    currency = currency_field()
    supplier = models.ForeignKey(
        "crm.Account", null=True, blank=True, on_delete=models.SET_NULL, related_name="supplied_products"
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["tenant", "code"], name="product_unique_code")
        ]

    def __str__(self):
        return f"{self.code} {self.name}"


class Pricelist(BaseModel):
    """Default pricelist with effective dates; customer pricelists are out of v1 (CRM-02.9)."""

    name = models.CharField(max_length=120)
    valid_from = models.DateField()
    valid_to = models.DateField(null=True, blank=True)
    is_default = models.BooleanField(default=True)
    currency = currency_field()

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(valid_to__isnull=True) | models.Q(valid_to__gte=models.F("valid_from")),
                name="pricelist_dates_ordered",
            )
        ]

    def __str__(self):
        return self.name


class PricelistItem(models.Model):
    pricelist = models.ForeignKey(Pricelist, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="pricelist_items")
    price = positive_money_field()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["pricelist", "product"], name="pricelistitem_unique")
        ]


class Asset(BaseModel):
    """Company-owned or managed item (AST-01.4); expiries drive reminders (AST-01.5)."""

    class Type(models.TextChoices):
        HARDWARE = "hardware", "Hardware"
        SOFTWARE = "software", "Software"
        LICENCE = "licence", "Licence"

    identifier = models.CharField(max_length=120)
    name = models.CharField(max_length=255)
    type = models.CharField(max_length=16, choices=Type.choices)
    serial_number = models.CharField(max_length=120, blank=True)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="assets"
    )
    location = models.CharField(max_length=255, blank=True)
    purchase_date = models.DateField(null=True, blank=True)
    warranty_end = models.DateField(null=True, blank=True, db_index=True)
    licence_expiry = models.DateField(null=True, blank=True, db_index=True)
    product = models.ForeignKey(
        Product, null=True, blank=True, on_delete=models.SET_NULL, related_name="assets"
    )
    purchase_cost = money_field()
    currency = currency_field()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "identifier"], name="asset_unique_identifier")
        ]

    def __str__(self):
        return f"{self.identifier} {self.name}"
