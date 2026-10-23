from django.apps import AppConfig


class PlatformConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.platform"
    label = "platform"
    verbose_name = "Platform"

    def ready(self):
        from apps.platform import audit

        audit.connect()
