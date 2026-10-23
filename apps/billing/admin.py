from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError

from apps.admin_utils import register_all
from apps.billing import lifecycle, payments
from apps.billing.models import BillingDraft, DraftLine, Payment, PaymentAllocation


def _run(modeladmin, request, queryset, fn, done):
    ok = 0
    for obj in queryset:
        try:
            fn(obj, request.user)
            ok += 1
        except (ValidationError, PermissionDenied) as exc:
            msg = "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
            modeladmin.message_user(request, f"{obj}: {msg}", messages.ERROR)
    if ok:
        modeladmin.message_user(request, f"{ok} {done}.", messages.SUCCESS)


class DraftLineInline(admin.TabularInline):
    model = DraftLine
    extra = 0
    fields = ["position", "description", "project", "qty", "unit_price", "tax_category", "net", "tax", "gross"]
    readonly_fields = ["net", "tax", "gross"]


class AllocationInline(admin.TabularInline):
    model = PaymentAllocation
    extra = 0
    readonly_fields = ["payment", "draft", "amount", "created_at"]
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(BillingDraft)
class BillingDraftAdmin(admin.ModelAdmin):
    list_display = ["number", "account", "status", "issue_date", "due_date", "gross", "outstanding", "payment_status"]
    list_filter = ["tenant", "status", "payment_status"]
    search_fields = ["number", "account__legal_name", "external_invoice_no"]
    inlines = [DraftLineInline, AllocationInline]
    readonly_fields = ["status", "net", "tax", "gross", "paid", "outstanding", "payment_status",
                       "approved_by", "approved_at", "reissued_from"]
    actions = ["approve", "reopen", "send", "reissue"]

    @admin.action(description="Approve selected drafts")
    def approve(self, request, queryset):
        _run(self, request, queryset, lifecycle.approve, "approved")

    @admin.action(description="Reopen selected approved drafts")
    def reopen(self, request, queryset):
        _run(self, request, queryset, lifecycle.reopen, "reopened")

    @admin.action(description="Mark selected as sent to accounting")
    def send(self, request, queryset):
        _run(self, request, queryset, lifecycle.send_to_accounting, "sent to accounting")

    @admin.action(description="Reissue selected voided drafts")
    def reissue(self, request, queryset):
        _run(self, request, queryset, lifecycle.reissue, "reissued")


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ["date", "account", "amount", "method", "reference"]
    list_filter = ["tenant", "method"]
    inlines = [AllocationInline]
    actions = ["auto_allocate"]

    @admin.action(description="Allocate to open drafts, oldest due first")
    def auto_allocate(self, request, queryset):
        _run(self, request, queryset, payments.auto_allocate, "payments allocated")


register_all("billing")
