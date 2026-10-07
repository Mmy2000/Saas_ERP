from django.urls import path
from rest_framework.routers import SimpleRouter

from .feed import FeedActionView, FeedView
from .views import FxRateViewSet, PriceBoardViewSet

router = SimpleRouter()
router.register("boards", PriceBoardViewSet, basename="price-board")
router.register("fx-rates", FxRateViewSet, basename="fx-rate")

urlpatterns = [
    path("feed/", FeedView.as_view(), name="price-feed"),
    path("feed/action/", FeedActionView.as_view(), name="price-feed-action"),
    *router.urls,
]
