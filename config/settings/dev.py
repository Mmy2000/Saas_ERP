from .base import *  # noqa: F403
from .base import env

DEBUG = env.bool("DJANGO_DEBUG", default=True)
SECRET_KEY = SECRET_KEY or "dev-insecure-secret-key-do-not-use-in-production"  # noqa: F405
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[".localhost", "localhost", "127.0.0.1"])
FIELD_ENCRYPTION_KEY = FIELD_ENCRYPTION_KEY or derived_dev_key(SECRET_KEY, "field")  # noqa: F405
BLIND_INDEX_KEY = BLIND_INDEX_KEY or derived_dev_key(SECRET_KEY, "blind")  # noqa: F405

# E-mails are written to files instead of being sent (set EMAIL_URL to really send them).
if not env("EMAIL_URL", default=""):
    EMAIL_BACKEND = "django.core.mail.backends.filebased.EmailBackend"
    EMAIL_FILE_PATH = BASE_DIR / "var" / "mail"  # noqa: F405
SITE_SCHEME = env("SITE_SCHEME", default="http")
SITE_PORT = env("SITE_PORT", default=":8000")
