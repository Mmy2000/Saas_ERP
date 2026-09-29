"""URLs served on platform hosts (PLATFORM_HOSTS). No tenant context exists here."""

from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path


def healthz(request):
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("healthz/", healthz),
    path("i18n/", include("django.conf.urls.i18n")),
    path("", admin.site.urls),
]
