from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class DiamondsConfig(AppConfig):
    name = "apps.diamonds"
    label = "diamonds"
    verbose_name = "Diamonds and gemstones"

    def ready(self):
        from apps.core.documents import register_document

        register_document("diamonds.StoneSetting", "stone-setting", _("Stone setting"))
