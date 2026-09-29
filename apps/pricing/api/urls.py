from rest_framework.routers import SimpleRouter

from .views import FxRateViewSet, PriceBoardViewSet

router = SimpleRouter()
router.register("boards", PriceBoardViewSet, basename="price-board")
router.register("fx-rates", FxRateViewSet, basename="fx-rate")

urlpatterns = router.urls
