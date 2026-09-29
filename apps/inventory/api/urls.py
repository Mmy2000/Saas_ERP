from rest_framework.routers import SimpleRouter

from .documents import StocktakeViewSet, StockTransferViewSet
from .views import ItemViewSet, LotViewSet

router = SimpleRouter()
router.register("items", ItemViewSet, basename="item")
router.register("lots", LotViewSet, basename="lot")
router.register("transfers", StockTransferViewSet, basename="stock-transfer-api")
router.register("stocktakes", StocktakeViewSet, basename="stocktake-api")

urlpatterns = router.urls
