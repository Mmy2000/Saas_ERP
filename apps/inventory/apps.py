from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class InventoryConfig(AppConfig):
    name = "apps.inventory"
    label = "inventory"
    verbose_name = "Inventory"

    def ready(self):
        from apps.core.documents import register_document

        register_document("inventory.StockTransfer", "stock-transfer", _("Stock transfer"))
        register_document("inventory.Stocktake", "stocktake", _("Stocktake"))
