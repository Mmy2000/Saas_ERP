from django.urls import path
from rest_framework.routers import SimpleRouter

from .returns import SupplierReturnViewSet
from .scrap import QuoteView, ScrapPurchaseViewSet, ScrapSaleViewSet
from .views import SupplierInvoiceViewSet

router = SimpleRouter()
router.register("invoices", SupplierInvoiceViewSet, basename="supplier-invoice")
router.register("returns", SupplierReturnViewSet, basename="supplier-return-api")
router.register("scrap/purchases", ScrapPurchaseViewSet, basename="scrap-purchase-api")
router.register("scrap/sales", ScrapSaleViewSet, basename="scrap-sale-api")

urlpatterns = [
    path("scrap/quote/", QuoteView.as_view(), name="scrap-quote"),
    *router.urls,
]
