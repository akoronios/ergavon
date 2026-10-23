from django.contrib import admin
from unfold.admin import TabularInline

from apps.admin_utils import ErgavonModelAdmin, register_all
from apps.crm.models import Account, AccountRole, Contact, Membership
from apps.crm.search import normalise


class AccountRoleInline(TabularInline):
    model = AccountRole
    extra = 0
    tab = True


class MembershipInline(TabularInline):
    model = Membership
    extra = 0
    autocomplete_fields = ["contact"]
    fields = ["contact", "role", "is_primary", "start_date", "end_date"]
    tab = True


class ContactMembershipInline(TabularInline):
    model = Membership
    extra = 0
    autocomplete_fields = ["account"]
    fields = ["account", "role", "is_primary", "start_date", "end_date"]
    tab = True


@admin.register(Account)
class AccountAdmin(ErgavonModelAdmin):
    list_display = ["legal_name", "trading_name", "tax_id", "city", "owner"]
    list_filter = ["roles__role", "country", "owner", "tenant"]
    search_fields = ["legal_name", "trading_name", "tax_id", "email"]
    inlines = [AccountRoleInline, MembershipInline]
    readonly_fields = ["vies_status", "vies_checked_at", "created_at", "updated_at"]
    fieldsets = [
        (None, {"fields": ["tenant", "legal_name", "trading_name", "owner", "language"]}),
        ("Tax details", {"fields": ["tax_id", "tax_office", "gemi_number", "business_activity", "vies_status", "vies_checked_at"]}),
        ("Address and contact", {"fields": ["country", "address_line1", "address_line2", "city", "region", "postcode", "email", "phone", "website"]}),
        ("Record", {"fields": ["created_at", "updated_at"], "classes": ["collapse"]}),
    ]

    def get_search_results(self, request, queryset, search_term):
        """Match Greek names regardless of accents and case (CRM-01.6)."""
        results, may_have_duplicates = super().get_search_results(request, queryset, search_term)
        if search_term:
            results = results | queryset.filter(search_name__icontains=normalise(search_term))
        return results, may_have_duplicates


@admin.register(Contact)
class ContactAdmin(ErgavonModelAdmin):
    list_display = ["first_name", "last_name", "email", "phone", "mobile"]
    search_fields = ["first_name", "last_name", "email", "phone", "mobile"]
    list_filter = ["marketing_consent", "tenant"]
    readonly_fields = ["anonymised_at"]
    inlines = [ContactMembershipInline]


register_all("crm")
