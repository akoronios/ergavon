from django import forms
from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from unfold.decorators import action, display
from unfold.enums import ActionVariant
from unfold.forms import BaseDialogForm
from unfold.widgets import UnfoldAdminTextInputWidget

from apps.admin_utils import ErgavonModelAdmin, register_all
from apps.platform.models import Tenant
from apps.subscriptions.models import Domain, ScheduledRun, Subscription

SUB_LABELS = {
    "draft": "info", "active": "success", "suspended": "warning",
    "cancelled": "danger", "expired": "danger", "overdue_payment": "danger",
}


class DatePickerWidget(UnfoldAdminTextInputWidget):
    input_type = "date"


class RunBillingForm(BaseDialogForm):
    tenant = forms.ModelChoiceField(queryset=Tenant.objects.all(), empty_label=None)
    until = forms.DateField(label="Bill periods starting on or before", widget=DatePickerWidget)

    def __init__(self, request, object_id=None, *args, **kwargs):
        super().__init__(request, object_id, *args, **kwargs)
        self.fields["until"].initial = timezone.localdate()
        if getattr(request.user, "tenant_id", None):
            self.fields["tenant"].initial = request.user.tenant_id


def _is_admin(user):
    return user.is_superuser or user.has_role("admin")


@admin.register(ScheduledRun)
class ScheduledRunAdmin(ErgavonModelAdmin):
    list_display = ["started_at", "tenant", "kind", "period_end", "status_label", "records_created", "retry_of"]
    list_filter = ["kind", "status", "tenant"]
    readonly_fields = [f.name for f in ScheduledRun._meta.fields]
    actions = ["retry_selected"]
    actions_list = ["run_billing_now"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @display(description="Status", label={"running": "info", "succeeded": "success", "failed": "danger"})
    def status_label(self, obj):
        return obj.status, obj.get_status_display()

    @action(
        description="Run billing now",
        icon="play_arrow",
        variant=ActionVariant.PRIMARY,
        dialog={
            "title": "Run billing",
            "description": "Bills every due subscription period and domain renewal. Safe to repeat: nothing is billed twice.",
            "form_class": RunBillingForm,
            "form_submit_text": "Run billing",
        },
    )
    def run_billing_now(self, request, form):
        from apps.billing.billing_run import run_billing

        if not _is_admin(request.user):
            self.message_user(request, "Only an Admin can run billing.", messages.ERROR)
        else:
            result = run_billing(form.cleaned_data["tenant"], until=form.cleaned_data["until"], user=request.user)
            self.message_user(
                request,
                f"Billing run: {result.items_created} items, {result.drafts_created} drafts, {len(result.errors)} errors.",
                messages.ERROR if result.errors else messages.SUCCESS,
            )
        return redirect(reverse("admin:subscriptions_scheduledrun_changelist"))

    @admin.action(description="Retry selected billing runs")
    def retry_selected(self, request, queryset):
        from apps.billing.billing_run import retry_run

        if not _is_admin(request.user):
            self.message_user(request, "Only an Admin can retry runs.", messages.ERROR)
            return
        for run in queryset.filter(kind=ScheduledRun.Kind.BILLING):
            result = retry_run(run, request.user)
            self.message_user(
                request,
                f"Retried run of {run.period_end}: {result.items_created} items, "
                f"{result.drafts_created} drafts, {len(result.errors)} errors.",
                messages.ERROR if result.errors else messages.SUCCESS,
            )


@admin.register(Subscription)
class SubscriptionAdmin(ErgavonModelAdmin):
    list_display = ["__str__", "account", "recurrence", "agreed_price", "next_billing_date", "renewal_date", "status_label"]
    list_filter = ["status", "recurrence", "next_billing_date", "renewal_date", "tenant"]
    search_fields = ["description", "account__legal_name", "product__name", "product__code"]
    list_select_related = ["account", "product"]
    autocomplete_fields = ["account", "product"]

    @display(description="Status", label=SUB_LABELS)
    def status_label(self, obj):
        return obj.status, obj.get_status_display()


@admin.register(Domain)
class DomainAdmin(ErgavonModelAdmin):
    list_display = ["domain_name", "account", "registrar", "expiry_date", "next_due_date", "auto_renew", "status_label"]
    list_filter = ["status", "auto_renew", "registrar", "expiry_date", "tenant"]
    search_fields = ["domain_name", "account__legal_name"]
    list_select_related = ["account"]
    autocomplete_fields = ["account"]

    @display(description="Status", label={"pending": "info", "active": "success", "grace": "warning", "expired": "danger", "transferred_away": "danger", "cancelled": "danger"})
    def status_label(self, obj):
        return obj.status, obj.get_status_display()


register_all("subscriptions")
