from django.urls import path
from rest_framework.routers import SimpleRouter

from .reservations import ReservationViewSet
from .returns import SalesReturnViewSet
from .trade import TradeQuoteView, TradeReturnViewSet, TradeSaleViewSet
from .views import QuoteView, SalesInvoiceViewSet

router = SimpleRouter()
router.register("invoices", SalesInvoiceViewSet, basename="sales-invoice")
router.register("returns", SalesReturnViewSet, basename="sales-return")
router.register("reservations", ReservationViewSet, basename="sales-reservation")
router.register("trade", TradeSaleViewSet, basename="trade-sale-api")
router.register("trade-returns", TradeReturnViewSet, basename="trade-return-api")

urlpatterns = [
    path("quote/", QuoteView.as_view(), name="sales-quote"),
    path("trade/quote/", TradeQuoteView.as_view(), name="trade-quote"),
    *router.urls,
]
