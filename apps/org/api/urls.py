from rest_framework.routers import SimpleRouter

from .views import BranchViewSet

router = SimpleRouter()
router.register("branches", BranchViewSet, basename="branch")

urlpatterns = router.urls
