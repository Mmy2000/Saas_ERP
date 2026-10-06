"""Settings shared by every environment. Values that differ per machine come from `.env`."""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parents[2]

env = environ.Env()
if (BASE_DIR / ".env").exists():
    environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY", default="")
DEBUG = env.bool("DJANGO_DEBUG", default=False)

# Tenant hosts are `<slug>.<base>` or verified custom domains (TenantDomain). Platform hosts
# have no tenant and serve config.platform_urls (Django admin lives only there, §5.4).
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[".localhost"])
PLATFORM_HOSTS = env.list("PLATFORM_HOSTS", default=["admin.localhost"])
ROOT_URLCONF = "config.urls"
PLATFORM_URLCONF = "config.platform_urls"
# New clients get `<slug>.<TENANT_BASE_DOMAIN>` (custom domains can be added in the console).
TENANT_BASE_DOMAIN = env("TENANT_BASE_DOMAIN", default="localhost")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "django_filters",
    "drf_spectacular",
    "apps.core",
    "apps.platform.tenants",
    "apps.platform.monitor",
    "apps.platform.jobs",
    "apps.iam",
    "apps.org",
    "apps.catalog",
    "apps.pricing",
    "apps.parties",
    "apps.ledger",
    "apps.inventory",
    "apps.purchasing",
    "apps.sales",
    "apps.settlements",
    "apps.treasury",
    "apps.expenses",
    "apps.printing",
    "apps.manufacturing",
    "apps.repairs",
    "apps.hr",
    "apps.reports",
    "apps.diamonds",
    "apps.audit",
    "apps.messaging",
]

MIDDLEWARE = [
    "apps.core.middleware.RequestIdMiddleware",
    "django.middleware.security.SecurityMiddleware",
    # Counts requests per client and answers 429 over the limit, outside the tenant transaction.
    "apps.platform.tenants.middleware.TrafficMiddleware",
    # Opens the request transaction and runs SET LOCAL app.tenant_id; everything below runs in it.
    "apps.core.tenancy.middleware.TenantResolutionMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "apps.core.i18n.TenantLocaleMiddleware",  # cookie → tenant default → LANGUAGE_CODE
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.iam.middleware.TenantMembershipMiddleware",
    "apps.audit.middleware.AuditUserMiddleware",  # SET LOCAL app.user_id for the audit trail
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.template.context_processors.i18n",
                "apps.core.context_processors.shell",
                "apps.core.appearance.appearance",
                "apps.core.branding.branding",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# Runtime connects as app_rw (subject to RLS). Migrations run as app_owner through the
# "owner" alias: `manage.py migrate --database owner`. See ops/db/bootstrap.sql.
# ATOMIC_REQUESTS stays False: TenantResolutionMiddleware opens the request transaction itself,
# because SET LOCAL must run inside it and ATOMIC_REQUESTS only wraps the view.
DATABASES = {"default": env.db("DATABASE_URL", default="postgres://app_rw@localhost:5432/gweb")}
if env("DATABASE_OWNER_URL", default=""):
    DATABASES["owner"] = env.db("DATABASE_OWNER_URL")
    DATABASES["owner"]["TEST"] = {"MIRROR": "default"}
for _db in DATABASES.values():
    _db["ATOMIC_REQUESTS"] = False
    _db.setdefault("CONN_MAX_AGE", env.int("DB_CONN_MAX_AGE", default=0))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "iam.User"
AUTHENTICATION_BACKENDS = [
    "apps.iam.backends.TenantMembershipBackend",  # tenant hosts: tenant username or email
    "apps.iam.backends.PlatformEmailBackend",  # platform host only: staff email
]
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    # Legacy Gweb hashes are PBKDF2; they are accepted and upgraded to Argon2 on login.
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 10},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"

# Host-only cookies (no Domain attribute): a session from a.example is never sent to b.example.
SESSION_COOKIE_DOMAIN = None
CSRF_COOKIE_DOMAIN = None
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 12 * 60 * 60

LANGUAGE_CODE = "ar"
LANGUAGES = [("ar", "العربية"), ("en", "English")]
LOCALE_PATHS = [BASE_DIR / "locale"]
FORMAT_MODULE_PATH = ["config.formats"]
LANGUAGE_COOKIE_AGE = 365 * 24 * 60 * 60
LANGUAGE_COOKIE_SAMESITE = "Lax"
TIME_ZONE = "Africa/Cairo"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "var" / "static"
# Uploads, one folder per client (tenants/<id>/) plus platform/. Served only through
# apps.core.media.serve, which checks host and user: never point a web server straight at it.
MEDIA_URL = "/media/"
MEDIA_ROOT = env.path("MEDIA_ROOT", default=BASE_DIR / "var" / "media")

# Server monitoring (platform console → Server). `manage.py monitor_server` records a sample
# every MONITOR_SAMPLE_SECONDS; the page also records while it is open. Thresholds are in %.
MONITOR_SAMPLE_SECONDS = env.int("MONITOR_SAMPLE_SECONDS", default=10)
MONITOR_RETENTION_DAYS = env.int("MONITOR_RETENTION_DAYS", default=7)
MONITOR_WARN_PCT = env.int("MONITOR_WARN_PCT", default=75)
MONITOR_CRITICAL_PCT = env.int("MONITOR_CRITICAL_PCT", default=90)

# Client traffic (platform console → Traffic). Each client's limit can be changed there; this
# is the default for clients without one. 0 = no limit.
TENANT_REQUESTS_PER_MINUTE = env.int("TENANT_REQUESTS_PER_MINUTE", default=600)
TRAFFIC_SLOW_MS = env.int("TRAFFIC_SLOW_MS", default=1000)  # a request this slow counts as slow

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["apps.core.api.permissions.HasTenantPermission"],
    "DEFAULT_PAGINATION_CLASS": "apps.core.api.pagination.StandardPagination",
    "DEFAULT_FILTER_BACKENDS": ["django_filters.rest_framework.DjangoFilterBackend"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "apps.core.api.exceptions.exception_handler",
    "DEFAULT_RENDERER_CLASSES": [
        "apps.core.api.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
    "COERCE_DECIMAL_TO_STRING": True,
}
SPECTACULAR_SETTINGS = {
    "TITLE": "Gweb Platform API",
    "VERSION": "v1",
    "SERVE_INCLUDE_SCHEMA": False,
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")},
}

# PII encryption (apps.core.crypto). Keep these outside the database and back them up:
# losing FIELD_ENCRYPTION_KEY makes encrypted values (national IDs) unreadable.
FIELD_ENCRYPTION_KEY = env("FIELD_ENCRYPTION_KEY", default="")
BLIND_INDEX_KEY = env("BLIND_INDEX_KEY", default="")


def derived_dev_key(secret: str, purpose: str) -> str:
    """Development/test fallback only: a stable key derived from SECRET_KEY."""
    import base64
    import hashlib

    digest = hashlib.sha256(f"{purpose}:{secret}".encode()).digest()
    return base64.urlsafe_b64encode(digest).decode()

# Background jobs (apps.platform.jobs): `manage.py run_worker` waits this long when idle.
JOBS_POLL_SECONDS = env.float("JOBS_POLL_SECONDS", default=2)

# E-mail: documents sent to customers and the daily reminders. EMAIL_URL is e.g.
# smtp+tls://user:password@smtp.example.com:587 (development writes them to var/mail).
_email = env.email_url("EMAIL_URL", default="smtp://localhost:25")
EMAIL_BACKEND = _email["EMAIL_BACKEND"]
EMAIL_HOST = _email.get("EMAIL_HOST", "localhost")
EMAIL_PORT = _email.get("EMAIL_PORT", 25)
EMAIL_HOST_USER = _email.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = _email.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = _email.get("EMAIL_USE_TLS", False)
EMAIL_USE_SSL = _email.get("EMAIL_USE_SSL", False)
EMAIL_TIMEOUT = 30
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="no-reply@localhost")
# Links in e-mails point at the client's primary domain with this scheme (and port, if any).
SITE_SCHEME = env("SITE_SCHEME", default="https")
SITE_PORT = env("SITE_PORT", default="")
