from rest_framework.routers import SimpleRouter

from .views import CurrencyViewSet, ItemCategoryViewSet, KaratViewSet

router = SimpleRouter()
router.register("currencies", CurrencyViewSet, basename="currency")
router.register("karats", KaratViewSet, basename="karat")
router.register("categories", ItemCategoryViewSet, basename="item-category")

urlpatterns = router.urls
