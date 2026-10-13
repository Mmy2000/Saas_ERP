from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .base import env

DEBUG = False
if not SECRET_KEY:  # noqa: F405
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set in production.")

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# The visitor address our reverse proxy appends, for the sign-in history (apps/iam/signals.py).
SIGNIN_IP_HEADER = env.str("SIGNIN_IP_HEADER", default="HTTP_X_FORWARDED_FOR")
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=60 * 60 * 24 * 30)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True  # every tenant is an HTTPS subdomain
SECURE_HSTS_PRELOAD = False  # after stabilization (§24)
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
X_FRAME_OPTIONS = "DENY"
if not (FIELD_ENCRYPTION_KEY and BLIND_INDEX_KEY):  # noqa: F405
    raise ImproperlyConfigured(
        "FIELD_ENCRYPTION_KEY and BLIND_INDEX_KEY must be set in production.")

# Every web and worker process must see the same cache: a feature switched in the console, the
# per-client request limits. A file cache does it on one server; several servers need a shared
# one (CACHE_URL=rediscache://… with the redis package, or dbcache://gweb_cache after
# `manage.py createcachetable --database owner`).
CACHES = {"default": env.cache("CACHE_URL") if env("CACHE_URL", default="") else {
    "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
    "LOCATION": BASE_DIR / "var" / "cache",  # noqa: F405
}}
