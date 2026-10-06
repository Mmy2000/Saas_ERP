from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class TreasuryConfig(AppConfig):
    name = "apps.treasury"
    label = "treasury"
    verbose_name = "Treasury"

    def ready(self):
        from apps.core.documents import register_document

        register_document("treasury.TreasuryDocument", "treasury-document",
                          lambda doc: doc.title)
        register_document("treasury.Cheque", "cheque", _("Cheque"))
        register_document("treasury.CashCount", "cash-count", _("Cash count"))
