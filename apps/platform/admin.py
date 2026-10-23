from django.contrib import admin

from apps.admin_utils import ErgavonModelAdmin, register_all
from apps.platform.models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(ErgavonModelAdmin):
    """Read-only: the audit trail is written by the system, never edited."""

    list_display = ["at", "user", "action", "content_type", "object_id", "note"]
    list_filter = ["action", "content_type", "user", "at", "tenant"]
    search_fields = ["object_id", "note"]
    date_hierarchy = "at"
    readonly_fields = [f.name for f in AuditLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


register_all("platform")
