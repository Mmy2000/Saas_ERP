from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView

from apps.core.api.permissions import HasTenantPermission
from apps.iam.catalog import AUTHENTICATED


class SchemaView(SpectacularAPIView):
    # drf-spectacular sets its own permission_classes (AllowAny by default), which would
    # skip the project's default and publish the schema to anyone: keep ours.
    permission_classes = [HasTenantPermission]
    required_permissions = {"GET": AUTHENTICATED}


urlpatterns = [
    path("schema/", SchemaView.as_view(), name="api-schema"),
    path("iam/", include("apps.iam.api.urls")),
    path("org/", include("apps.org.api.urls")),
    path("catalog/", include("apps.catalog.api.urls")),
    path("pricing/", include("apps.pricing.api.urls")),
    path("parties/", include("apps.parties.api.urls")),
    path("ledger/", include("apps.ledger.api.urls")),
    path("inventory/", include("apps.inventory.api.urls")),
    path("purchasing/", include("apps.purchasing.api.urls")),
    path("sales/", include("apps.sales.api.urls")),
    path("settlements/", include("apps.settlements.api")),
    path("treasury/", include("apps.treasury.api")),
    path("expenses/", include("apps.expenses.api")),
    path("printing/", include("apps.printing.api")),
    path("manufacturing/", include("apps.manufacturing.api")),
    path("diamonds/", include("apps.diamonds.api")),
    path("messages/", include("apps.messaging.api")),
    path("notifications/", include("apps.notifications.api")),
    path("opening/", include("apps.opening.api")),
    path("merchandising/", include("apps.merchandising.api")),
    path("repairs/", include("apps.repairs.api")),
    path("hr/", include("apps.hr.api")),
]
