from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView

from apps.iam.catalog import AUTHENTICATED


class SchemaView(SpectacularAPIView):
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
    path("repairs/", include("apps.repairs.api")),
]
