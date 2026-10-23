"""ERGAVON settings. Every deployment-specific value comes from the environment (.env)."""

from pathlib import Path

import environ
from django.urls import reverse_lazy

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
)
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY", default="dev-only-change-me")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

INSTALLED_APPS = [
    # Unfold admin theme: must come before django.contrib.admin
    "unfold",
    "unfold.contrib.filters",
    "unfold.contrib.forms",
    "unfold.contrib.inlines",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # ERGAVON apps (Data Model v0.1)
    "apps.platform",
    "apps.crm",
    "apps.catalogue",
    "apps.sales",
    "apps.subscriptions",
    "apps.projects",
    "apps.billing",
    "apps.support",
    "apps.calendar",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # serves the admin's static files without extra server setup
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.platform.audit.AuditUserMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# PostgreSQL, e.g. DATABASE_URL=postgres://ergavon:secret@localhost:5432/ergavon
DATABASES = {"default": env.db("DATABASE_URL", default="postgres://ergavon@localhost:5432/ergavon")}
DATABASES["default"]["ATOMIC_REQUESTS"] = True

AUTH_USER_MODEL = "platform.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Store UTC, display in each user's timezone (CAL-01.4)
USE_TZ = True
TIME_ZONE = "Europe/Athens"
USE_I18N = True
LANGUAGE_CODE = "en"
LANGUAGES = [("en", "English"), ("el", "Ελληνικά")]
LOCALE_PATHS = [BASE_DIR / "locale"]

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    # Hashed, compressed files in production (run collectstatic); plain files while developing
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        if DEBUG
        else "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}
MEDIA_URL = "media/"
MEDIA_ROOT = env("MEDIA_ROOT", default=str(BASE_DIR / "media"))  # attachments live outside the DB

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
DATA_UPLOAD_MAX_MEMORY_SIZE = 25 * 1024 * 1024

# Full origins of the site, e.g. https://ergavon.example.com (needed for admin forms over HTTPS)
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])

if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=3600)
    # Plesk redirects HTTP to HTTPS itself and Passenger passes the scheme straight
    # to Django, so both are off by default. Turn them on only behind a proxy that
    # sends X-Forwarded-Proto, otherwise every request loops on a redirect.
    if env.bool("BEHIND_PROXY", default=False):
        SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=False)


# ---------------------------------------------------------------- admin (Unfold)


def _nav(title, icon, name):
    return {"title": title, "icon": icon, "link": reverse_lazy(f"admin:{name}_changelist")}


UNFOLD = {
    "SITE_TITLE": "ERGAVON",
    "SITE_HEADER": "ERGAVON",
    "SITE_SUBHEADER": "Billing and operations",
    "SITE_SYMBOL": "receipt_long",
    "SITE_URL": "/admin/",
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": False,
    "ENVIRONMENT": "apps.platform.dashboard.environment_callback",
    "DASHBOARD_CALLBACK": "apps.platform.dashboard.dashboard_callback",
    "COLORS": {
        "primary": {
            "50": "oklch(97.7% .014 245)", "100": "oklch(95.1% .026 245)", "200": "oklch(90.1% .058 245)",
            "300": "oklch(82.8% .111 245)", "400": "oklch(74.6% .16 245)", "500": "oklch(68.5% .169 245)",
            "600": "oklch(58.8% .158 245)", "700": "oklch(50% .134 245)", "800": "oklch(44.3% .11 245)",
            "900": "oklch(39.1% .09 245)", "950": "oklch(29.3% .066 245)",
        },
    },
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": True,
        "navigation": [
            {
                "title": "Billing",
                "separator": True,
                "items": [
                    {"title": "Dashboard", "icon": "dashboard", "link": reverse_lazy("admin:index")},
                    _nav("Billing drafts", "receipt_long", "billing_billingdraft"),
                    _nav("Payments", "payments", "billing_payment"),
                    _nav("Billable items", "list_alt", "billing_billableitem"),
                    _nav("Scheduled runs", "schedule", "subscriptions_scheduledrun"),
                ],
            },
            {
                "title": "Customers",
                "separator": True,
                "items": [
                    _nav("Accounts", "apartment", "crm_account"),
                    _nav("Contacts", "contacts", "crm_contact"),
                    _nav("Leads", "person_search", "crm_lead"),
                    _nav("Opportunities", "trending_up", "crm_opportunity"),
                    _nav("Activities", "event_note", "crm_activity"),
                ],
            },
            {
                "title": "Services",
                "separator": True,
                "items": [
                    _nav("Subscriptions", "autorenew", "subscriptions_subscription"),
                    _nav("Domains", "language", "subscriptions_domain"),
                    _nav("Products", "inventory_2", "catalogue_product"),
                    _nav("Quotes", "request_quote", "sales_quote"),
                    _nav("Orders", "shopping_cart", "sales_order"),
                ],
            },
            {
                "title": "Work",
                "separator": True,
                "items": [
                    _nav("Projects", "folder_open", "projects_project"),
                    _nav("Tasks", "task_alt", "projects_task"),
                    _nav("Time entries", "timer", "projects_timeentry"),
                    _nav("Tickets", "support_agent", "support_ticket"),
                    _nav("Calendar events", "calendar_month", "calendar_event"),
                ],
            },
            {
                "title": "Settings",
                "separator": True,
                "collapsible": True,
                "items": [
                    _nav("Tax categories", "percent", "catalogue_taxcategory"),
                    _nav("Assets", "devices", "catalogue_asset"),
                    _nav("SLA policies", "timer_10_alert", "support_slapolicy"),
                    _nav("Users", "person", "platform_user"),
                    _nav("Custom fields", "tune", "platform_customfield"),
                    _nav("Audit log", "history", "platform_auditlog"),
                ],
            },
        ],
    },
}
