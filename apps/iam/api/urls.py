from django.urls import path
from rest_framework.routers import SimpleRouter

from .views import MemberViewSet, MeView, RoleViewSet

router = SimpleRouter()
router.register("members", MemberViewSet, basename="member")
router.register("roles", RoleViewSet, basename="role")

urlpatterns = [
    path("me/", MeView.as_view(), name="iam-me"),
    *router.urls,
]
