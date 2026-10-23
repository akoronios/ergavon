"""Register every model of an app in the Django admin with sensible defaults.

A stopgap for internal testing until the React UI exists.
"""

from django.apps import apps
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin


def register_all(app_label: str) -> None:
    for model in apps.get_app_config(app_label).get_models():
        if admin.site.is_registered(model):
            continue
        if app_label == "platform" and model.__name__ == "User":
            admin.site.register(model, UserAdmin)
            continue
        fields = [f.name for f in model._meta.fields if f.name not in ("id",)][:6]
        list_filter = [
            f.name for f in model._meta.fields if f.name in ("status", "type", "kind", "tenant")
        ]
        attrs = {"list_display": fields or ["__str__"], "list_filter": list_filter}
        if any(f.name == "number" for f in model._meta.fields):
            attrs["search_fields"] = ["number"]
        admin.site.register(model, type(f"{model.__name__}Admin", (admin.ModelAdmin,), attrs))
