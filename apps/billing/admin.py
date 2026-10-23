from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect
from django.urls import reverse
from unfold.admin import TabularInline
from unfold.decorators import action, display
from unfold.enums import ActionVariant
from unfold.forms import BaseDialogForm
from unfold.widgets import UnfoldAdminDecimalFieldWidget, UnfoldAdminSelectWidget, UnfoldAdminTextInputWidget

from apps.admin_utils import ErgavonModelAdmin, register_all
from apps.billing import lifecycle, payments
from apps.billing.models import BillableItem, BillingDraft, DraftLine, Payment, PaymentAllocation

PAYMENT_LABELS = {"unpaid": "danger", "partial": "warning", "paid": "success"}
STATUS_LABELS = {"draft": "info", "approved": "primary", "sent": "warning", "invoiced": "success", "voided": "danger"}


def _report(modeladmin, request, fn, done):
    """Run a service call and turn its outcome into an admin message."""
    try:
        result = fn()
    except ValidationError as exc:
        modeladmin.message_user(request, "; ".join(exc.messages), messages.ERROR)
        return None
    except PermissionDenied as exc:
        modeladmin.message_user(request, str(exc) or "Not allowed.", messages.ERROR)
        return None
    modeladmin.message_user(request, done, messages.SUCCESS)
    return result


def _bulk(modeladmin, request, queryset, fn, done):
    ok = 0
    for obj in queryset:
        if _report(modeladmin, request, lambda o=obj: fn(o, request.user), f"{obj}: {done}") is not None:
            ok += 1
    return ok


# ---------------------------------------------------------------- dialog forms


class DatePickerWidget(UnfoldAdminTextInputWidget):
    """Browser date picker: unambiguous, always sends YYYY-MM-DD."""

    input_type = "date"


class InvoicedForm(BaseDialogForm):
    external_invoice_no = forms.CharField(label="External invoice number", max_length=64, widget=UnfoldAdminTextInputWidget)
    external_invoice_date = forms.DateField(label="Invoice date", widget=DatePickerWidget)


class VoidForm(BaseDialogForm):
    reason = forms.CharField(label="Reason", max_length=255, widget=UnfoldAdminTextInputWidget)


class AllocateForm(BaseDialogForm):
    draft = forms.ModelChoiceField(label="Billing draft", queryset=BillingDraft.objects.none(), widget=UnfoldAdminSelectWidget)
    amount = forms.DecimalField(label="Amount", max_digits=14, decimal_places=2, widget=UnfoldAdminDecimalFieldWidget)

    def __init__(self, request, object_id=None, *args, **kwargs):
        super().__init__(request, object_id, *args, **kwargs)
        payment = Payment.objects.filter(pk=object_id).first()
        if payment:
            self.fields["draft"].queryset = payments.open_drafts(payment.account)
            self.fields["amount"].initial = payments.unallocated(payment)


# ---------------------------------------------------------------- inlines


class DraftLineInline(TabularInline):
    model = DraftLine
    extra = 0
    fields = ["position", "description", "project", "qty", "unit_price", "tax_category", "net", "tax", "gross"]
    readonly_fields = ["net", "tax", "gross"]
    tab = True

    def has_add_permission(self, request, obj=None):
        return obj is None or obj.status == BillingDraft.Status.DRAFT

    def has_change_permission(self, request, obj=None):
        return obj is None or obj.status == BillingDraft.Status.DRAFT

    def has_delete_permission(self, request, obj=None):
        return obj is None or obj.status == BillingDraft.Status.DRAFT


class AllocationInline(TabularInline):
    model = PaymentAllocation
    extra = 0
    fields = ["payment", "draft", "amount", "created_at"]
    readonly_fields = fields
    can_delete = False
    tab = True

    def has_add_permission(self, request, obj=None):
        return False


# ---------------------------------------------------------------- billing drafts


@admin.register(BillingDraft)
class BillingDraftAdmin(ErgavonModelAdmin):
    list_display = ["number", "account", "status_label", "issue_date", "due_date", "gross", "outstanding", "payment_label"]
    list_filter = ["status", "payment_status", "due_date", "tenant"]
    search_fields = ["number", "account__legal_name", "external_invoice_no"]
    list_select_related = ["account"]
    date_hierarchy = "issue_date"
    inlines = [DraftLineInline, AllocationInline]
    readonly_fields = [
        "status", "net", "tax", "gross", "paid", "outstanding", "payment_status",
        "external_invoice_no", "external_invoice_date", "approved_by", "approved_at", "reissued_from",
    ]
    fieldsets = [
        (None, {"fields": ["tenant", "number", "account", "status", "language", "currency"]}),
        ("Period and dates", {"fields": ["period_start", "period_end", "issue_date", "due_date"]}),
        ("Totals", {"fields": ["net", "tax", "gross", "paid", "outstanding", "payment_status"]}),
        ("Invoice and approval", {"fields": ["external_invoice_no", "external_invoice_date", "approved_by", "approved_at", "reissued_from"]}),
        ("Notes", {"fields": ["notes"]}),
    ]
    actions = ["approve_selected", "send_selected"]
    actions_detail = ["approve_one", "reopen_one", "send_one", "invoice_one", "void_one", "reissue_one"]

    def has_delete_permission(self, request, obj=None):
        return False  # financial records are voided, never deleted

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if obj and obj.status != BillingDraft.Status.DRAFT:
            fields += ["tenant", "number", "account", "language", "currency", "period_start", "period_end", "issue_date", "due_date"]
        if obj and obj.status in BillingDraft.LOCKED_STATUSES:
            fields += ["notes"]
        return fields

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        for obj in instances:
            if isinstance(obj, DraftLine):
                obj.compute()
            obj.save()
        for obj in formset.deleted_objects:
            obj.delete()
        formset.save_m2m()
        if isinstance(form.instance, BillingDraft) and form.instance.status == BillingDraft.Status.DRAFT:
            form.instance.recalculate()

    @display(description="Status", label=STATUS_LABELS)
    def status_label(self, obj):
        return obj.status, obj.get_status_display()

    @display(description="Payment", label=PAYMENT_LABELS)
    def payment_label(self, obj):
        return obj.payment_status, obj.get_payment_status_display()

    def _back(self, object_id):
        return redirect(reverse("admin:billing_billingdraft_change", args=[object_id]))

    # list actions
    @admin.action(description="Approve selected drafts")
    def approve_selected(self, request, queryset):
        _bulk(self, request, queryset, lifecycle.approve, "approved")

    @admin.action(description="Mark selected as sent to accounting")
    def send_selected(self, request, queryset):
        _bulk(self, request, queryset, lifecycle.send_to_accounting, "sent to accounting")

    # detail actions
    @action(description="Approve", icon="check_circle", variant=ActionVariant.PRIMARY)
    def approve_one(self, request, object_id):
        draft = BillingDraft.objects.get(pk=object_id)
        _report(self, request, lambda: lifecycle.approve(draft, request.user), f"{draft.number} approved.")
        return self._back(object_id)

    @action(description="Reopen", icon="undo")
    def reopen_one(self, request, object_id):
        draft = BillingDraft.objects.get(pk=object_id)
        _report(self, request, lambda: lifecycle.reopen(draft, request.user), f"{draft.number} is back in Draft.")
        return self._back(object_id)

    @action(description="Sent to accounting", icon="send")
    def send_one(self, request, object_id):
        draft = BillingDraft.objects.get(pk=object_id)
        _report(self, request, lambda: lifecycle.send_to_accounting(draft, request.user), f"{draft.number} sent to accounting.")
        return self._back(object_id)

    @action(
        description="Mark invoiced",
        icon="receipt_long",
        variant=ActionVariant.SUCCESS,
        dialog={"title": "Mark as invoiced", "description": "Enter the invoice issued by the accounting system. The draft is locked afterwards.", "form_class": InvoicedForm, "form_submit_text": "Mark invoiced"},
    )
    def invoice_one(self, request, form, object_id):
        draft = BillingDraft.objects.get(pk=object_id)
        data = form.cleaned_data
        _report(
            self, request,
            lambda: lifecycle.mark_invoiced(draft, request.user, data["external_invoice_no"], data["external_invoice_date"]),
            f"{draft.number} marked invoiced.",
        )
        return self._back(object_id)

    @action(
        description="Void",
        icon="block",
        variant=ActionVariant.DANGER,
        dialog={"title": "Void this draft", "description": "Its items go back to Uninvoiced and can be reissued.", "form_class": VoidForm, "form_submit_text": "Void draft"},
    )
    def void_one(self, request, form, object_id):
        draft = BillingDraft.objects.get(pk=object_id)
        _report(self, request, lambda: lifecycle.void(draft, request.user, form.cleaned_data["reason"]), f"{draft.number} voided.")
        return self._back(object_id)

    @action(description="Reissue", icon="content_copy")
    def reissue_one(self, request, object_id):
        draft = BillingDraft.objects.get(pk=object_id)
        new = _report(self, request, lambda: lifecycle.reissue(draft, request.user), f"{draft.number} reissued.")
        return self._back(new.pk if new else object_id)


# ---------------------------------------------------------------- payments


@admin.register(Payment)
class PaymentAdmin(ErgavonModelAdmin):
    list_display = ["date", "account", "amount", "unallocated_amount", "method", "reference"]
    list_filter = ["method", "date", "tenant"]
    search_fields = ["reference", "account__legal_name"]
    list_select_related = ["account"]
    date_hierarchy = "date"
    inlines = [AllocationInline]
    actions = ["auto_allocate_selected"]
    actions_detail = ["allocate_one", "auto_allocate_one"]

    def has_delete_permission(self, request, obj=None):
        return False

    def get_readonly_fields(self, request, obj=None):
        return ["account", "amount", "currency", "date"] if obj else []

    def save_model(self, request, obj, form, change):
        if not change:
            from apps.platform.permissions import FINANCE_ROLES, require_role

            require_role(request.user, *FINANCE_ROLES)
        super().save_model(request, obj, form, change)

    @display(description="Unallocated")
    def unallocated_amount(self, obj):
        return payments.unallocated(obj)

    def _back(self, object_id):
        return redirect(reverse("admin:billing_payment_change", args=[object_id]))

    @admin.action(description="Allocate to open drafts, oldest due first")
    def auto_allocate_selected(self, request, queryset):
        _bulk(self, request, queryset, payments.auto_allocate, "allocated")

    @action(
        description="Allocate to a draft",
        icon="call_split",
        variant=ActionVariant.PRIMARY,
        dialog={"title": "Allocate payment", "description": "Up to what is left on the payment and outstanding on the draft.", "form_class": AllocateForm, "form_submit_text": "Allocate"},
    )
    def allocate_one(self, request, form, object_id):
        payment = Payment.objects.get(pk=object_id)
        data = form.cleaned_data
        _report(self, request, lambda: payments.allocate(payment, data["draft"], data["amount"], request.user), "Payment allocated.")
        return self._back(object_id)

    @action(description="Auto-allocate", icon="auto_mode")
    def auto_allocate_one(self, request, object_id):
        payment = Payment.objects.get(pk=object_id)
        _report(self, request, lambda: payments.auto_allocate(payment, request.user), "Allocated to open drafts, oldest due first.")
        return self._back(object_id)


@admin.register(BillableItem)
class BillableItemAdmin(ErgavonModelAdmin):
    list_display = ["description", "account", "source", "period_start", "unit_price", "status_label", "project"]
    list_filter = ["source", "status", "period_start", "tenant"]
    search_fields = ["description", "account__legal_name"]
    list_select_related = ["account", "project"]

    def has_delete_permission(self, request, obj=None):
        return False

    @display(description="Status", label={"uninvoiced": "warning", "on_draft": "info", "billed": "success"})
    def status_label(self, obj):
        return obj.status, obj.get_status_display()


register_all("billing")
