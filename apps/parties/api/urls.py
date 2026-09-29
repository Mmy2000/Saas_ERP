from rest_framework.routers import SimpleRouter

from .views import CustomerViewSet, SupplierViewSet, TradeAccountViewSet

router = SimpleRouter()
router.register("customers", CustomerViewSet, basename="customer")
router.register("suppliers", SupplierViewSet, basename="supplier")
router.register("trade-accounts", TradeAccountViewSet, basename="trade-account")

urlpatterns = router.urls
