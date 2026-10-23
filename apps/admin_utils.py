"""Register every model of an app in the admin, using the Unfold theme.

The admin is the internal beta interface until the React UI exists. Apps with
hand-written admin classes register those first; this fills in the rest.
"""

from django.apps import apps
from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group
from unfold.admin import ModelAdmin
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm

STATUS_FIELDS = ("status", "type", "kind", "stage", "priority", "payment_status", "source")


class ErgavonModelAdmin(ModelAdmin):
    """Defaults for every ERGAVON admin page."""

    list_filter_submit = True
    list_per_page = 50
    warn_unsaved_form = True
    compressed_fields = True


class UserAdmin(BaseUserAdmin, ErgavonModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm
    list_display = ["username", "email", "first_name", "last_name", "tenant", "is_active", "is_superuser"]
    list_filter = ["tenant", "is_active", "is_superuser"]
    fieldsets = BaseUserAdmin.fieldsets + (
        ("ERGAVON", {"fields": ("tenant", "language", "timezone", "sso_provider", "mfa_enabled")}),
    )


class GroupAdmin(BaseGroupAdmin, ErgavonModelAdmin):
    pass


def _admin_for(model):
    names = [f.name for f in model._meta.fields]
    display = [n for n in names if n not in ("id", "tenant", "created_by", "updated_by", "archived_at")][:6]
    attrs = {
        "list_display": display or ["__str__"],
        "list_filter": [n for n in names if n in STATUS_FIELDS] + (["tenant"] if "tenant" in names else []),
    }
    search = [n for n in ("number", "name", "legal_name", "title", "subject", "domain_name", "code", "email") if n in names]
    if search:
        attrs["search_fields"] = search
    if "archived_at" in names:
        attrs["list_filter"].append("archived_at")
    readonly = [n for n in ("created_at", "updated_at", "created_by", "updated_by") if n in names]
    if readonly:
        attrs["readonly_fields"] = readonly
    return type(f"{model.__name__}Admin", (ErgavonModelAdmin,), attrs)


def register_all(app_label: str) -> None:
    if app_label == "platform":
        if admin.site.is_registered(Group):
            admin.site.unregister(Group)
        admin.site.register(Group, GroupAdmin)
    for model in apps.get_app_config(app_label).get_models():
        if admin.site.is_registered(model):
            continue
        if app_label == "platform" and model.__name__ == "User":
            admin.site.register(model, UserAdmin)
            continue
        admin.site.register(model, _admin_for(model))
