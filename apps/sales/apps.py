from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class SalesConfig(AppConfig):
    name = "apps.sales"
    label = "sales"
    verbose_name = "Sales"

    def ready(self):
        from apps.core.documents import register_document

        register_document("sales.SalesInvoice", "sale-detail", _("Sales invoice"))
        register_document("sales.SalesReturn", "sale-return-detail", _("Customer return"))
        register_document("sales.Reservation", "reservation", _("Reservation"))
        register_document("sales.TradeSale", "trade-sale", _("Wholesale"))
        register_document("sales.TradeReturn", "trade-return", _("Wholesale return"))
