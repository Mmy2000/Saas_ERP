from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class PurchasingConfig(AppConfig):
    name = "apps.purchasing"
    label = "purchasing"
    verbose_name = "Purchasing"

    def ready(self):
        from apps.core.documents import register_document

        register_document("purchasing.SupplierInvoice", "invoice-detail", _("Supplier invoice"))
        register_document("purchasing.SupplierReturn", "supplier-return", _("Return to supplier"))
        register_document("purchasing.ScrapPurchase", "scrap-purchase", _("Scrap purchase"))
        register_document("purchasing.ScrapSale", "scrap-sale", _("Scrap sale"))
