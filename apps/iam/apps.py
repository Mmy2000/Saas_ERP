from django.apps import AppConfig
from django.utils.module_loading import autodiscover_modules


class IamConfig(AppConfig):
    name = "apps.iam"
    label = "iam"
    verbose_name = "Identity and access"

    def ready(self):
        from . import signals  # noqa: F401

        autodiscover_modules("permissions")  # fills the permission catalog
