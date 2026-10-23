from django.contrib import admin, messages

from apps.admin_utils import register_all
from apps.subscriptions.models import ScheduledRun


@admin.register(ScheduledRun)
class ScheduledRunAdmin(admin.ModelAdmin):
    list_display = ["started_at", "tenant", "kind", "period_end", "status", "records_created", "retry_of"]
    list_filter = ["tenant", "kind", "status"]
    readonly_fields = [f.name for f in ScheduledRun._meta.fields]
    actions = ["retry_selected"]

    def has_add_permission(self, request):
        return False

    @admin.action(description="Retry selected billing runs")
    def retry_selected(self, request, queryset):
        from apps.billing.billing_run import retry_run

        if not (request.user.is_superuser or request.user.has_role("admin")):
            self.message_user(request, "Only an Admin can retry runs.", messages.ERROR)
            return
        for run in queryset.filter(kind=ScheduledRun.Kind.BILLING):
            result = retry_run(run, request.user)
            level = messages.ERROR if result.errors else messages.SUCCESS
            self.message_user(
                request,
                f"Retried run of {run.period_end}: {result.items_created} items, "
                f"{result.drafts_created} drafts, {len(result.errors)} errors.",
                level,
            )


register_all("subscriptions")
