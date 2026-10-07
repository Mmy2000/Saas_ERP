from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class OpeningConfig(AppConfig):
    name = "apps.opening"
    label = "opening"
    verbose_name = "Opening balances"

    def ready(self):
        from apps.core.documents import register_document

        register_document("opening.OpeningBalance", "opening-balance", _("Opening balances"))
