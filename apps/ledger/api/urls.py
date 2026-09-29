from django.urls import path
from rest_framework.routers import SimpleRouter

from .views import AccountViewSet, CommodityViewSet, EntryViewSet, PartyLookupView, TrialBalanceView

router = SimpleRouter()
router.register("accounts", AccountViewSet, basename="ledger-account")
router.register("commodities", CommodityViewSet, basename="ledger-commodity")
router.register("entries", EntryViewSet, basename="ledger-entry")

urlpatterns = [
    path("trial-balance/", TrialBalanceView.as_view(), name="ledger-trial-balance"),
    path("parties/", PartyLookupView.as_view(), name="ledger-party-lookup"),
    *router.urls,
]
