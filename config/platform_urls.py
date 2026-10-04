"""URLs served on platform hosts (PLATFORM_HOSTS). No tenant context exists here: the console
reads a client's data only inside that client's tenant_context."""

from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.http import JsonResponse
from django.urls import include, path

from apps.core import media
from apps.platform.console import views as console


def healthz(request):
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("healthz/", healthz),
    path("i18n/", include("django.conf.urls.i18n")),
    path("media/<path:path>", media.serve, name="media"),
    path("login/", console.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(next_page="login"), name="logout"),
    path("", console.home, name="home"),
    path("clients/", console.tenants, name="console-tenants"),
    path("clients/new/", console.tenant_new, name="console-tenant-new"),
    path("clients/<int:pk>/", console.tenant, name="console-tenant"),
    path("clients/<int:pk>/settings/", console.tenant_settings, name="console-tenant-settings"),
    path("clients/<int:pk>/profile/", console.tenant_profile, name="console-tenant-profile"),
    path("clients/<int:pk>/status/", console.tenant_status, name="console-tenant-status"),
    path("clients/<int:pk>/domains/", console.domain_add, name="console-domain-add"),
    path("clients/<int:pk>/domains/<int:domain_id>/remove/", console.domain_remove,
         name="console-domain-remove"),
    path("clients/<int:pk>/traffic/", console.tenant_traffic, name="console-tenant-traffic"),
    path("clients/<int:pk>/traffic/live/", console.tenant_traffic_live,
         name="console-tenant-traffic-live"),
    path("clients/<int:pk>/access/", console.tenant_access, name="console-tenant-access"),
    path("traffic/", console.traffic, name="console-traffic"),
    path("traffic/live/", console.traffic_live, name="console-traffic-live"),
    path("settings/", console.platform_settings, name="console-settings"),
    path("plans/", console.plans, name="console-plans"),
    path("plans/new/", console.plan_new, name="console-plan-new"),
    path("plans/<int:pk>/", console.plan_edit, name="console-plan-edit"),
    path("plans/<int:pk>/delete/", console.plan_delete, name="console-plan-delete"),
    path("features/", console.features, name="console-features"),
    path("features/plan/", console.plan_feature, name="console-plan-feature"),
    path("clients/<int:pk>/features/", console.tenant_feature, name="console-tenant-feature"),
    path("clients/<int:pk>/documents/<str:doc_type>/", console.tenant_document,
         name="console-tenant-document"),
    path("clients/<int:pk>/documents/<str:doc_type>/preview/", console.tenant_document_preview,
         name="console-tenant-document-preview"),
    path("clients/<int:pk>/documents/<str:doc_type>/designer/", console.tenant_designer,
         name="console-tenant-designer"),
    path("clients/<int:pk>/documents/<str:doc_type>/designer/preview/",
         console.tenant_designer_preview, name="console-tenant-designer-preview"),
    path("clients/<int:pk>/documents/<str:doc_type>/designer/save/",
         console.tenant_designer_save, name="console-tenant-designer-save"),
    path("clients/<int:pk>/documents/<str:doc_type>/versions/<int:version_id>/restore/",
         console.tenant_document_restore, name="console-tenant-document-restore"),
    path("activity/", console.events, name="console-events"),
    # The raw Django admin, for emergencies.
    path("django-admin/", admin.site.urls),
]
