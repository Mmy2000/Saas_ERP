from .base import *  # noqa: F403
from .base import env

DEBUG = env.bool("DJANGO_DEBUG", default=True)
SECRET_KEY = SECRET_KEY or "dev-insecure-secret-key-do-not-use-in-production"  # noqa: F405
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[".localhost", "localhost", "127.0.0.1"])
FIELD_ENCRYPTION_KEY = FIELD_ENCRYPTION_KEY or derived_dev_key(SECRET_KEY, "field")  # noqa: F405
BLIND_INDEX_KEY = BLIND_INDEX_KEY or derived_dev_key(SECRET_KEY, "blind")  # noqa: F405
