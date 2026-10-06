from django.urls import path
from rest_framework.routers import SimpleRouter

from .views import (
    AccountViewSet,
    CommodityViewSet,
    EntryViewSet,
    PartyLookupView,
    PeriodCloseView,
    PeriodReopenView,
    TrialBalanceView,
    YearCloseView,
    YearReopenView,
)

router = SimpleRouter()
router.register("accounts", AccountViewSet, basename="ledger-account")
router.register("commodities", CommodityViewSet, basename="ledger-commodity")
router.register("entries", EntryViewSet, basename="ledger-entry")

urlpatterns = [
    path("trial-balance/", TrialBalanceView.as_view(), name="ledger-trial-balance"),
    path("parties/", PartyLookupView.as_view(), name="ledger-party-lookup"),
    path("periods/close/", PeriodCloseView.as_view(), name="ledger-period-close"),
    path("periods/reopen/", PeriodReopenView.as_view(), name="ledger-period-reopen"),
    path("years/close/", YearCloseView.as_view(), name="ledger-year-close"),
    path("years/reopen/", YearReopenView.as_view(), name="ledger-year-reopen"),
    *router.urls,
]
