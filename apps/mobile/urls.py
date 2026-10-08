"""The mobile app's addresses, exactly as Gweb served them (the app is not changed)."""

from django.urls import path

from . import auth_api, inventory_api, stocktake_api

auth = [
    path("login/", auth_api.LoginView.as_view(), name="mobile-login"),
    path("refresh/", auth_api.RefreshView.as_view(), name="mobile-refresh"),
    path("logout/", auth_api.LogoutView.as_view(), name="mobile-logout"),
    path("me/", auth_api.MeView.as_view(), name="mobile-me"),
    path("change-password/", auth_api.ChangePasswordView.as_view(),
         name="mobile-change-password"),
]

inventory = [  # summary/ and branches/ before a piece's code
    path("inventory/", inventory_api.InventoryView.as_view(), name="mobile-inventory"),
    path("inventory/summary/", inventory_api.SummaryView.as_view(),
         name="mobile-inventory-summary"),
    path("inventory/branches/", inventory_api.BranchesView.as_view(),
         name="mobile-inventory-branches"),
    path("inventory/<str:code>/", inventory_api.PieceView.as_view(), name="mobile-piece"),
]

stocktake = [
    path("ghu11/", stocktake_api.StateView.as_view(), name="mobile-stocktake"),
    path("ghu11/lookups/", stocktake_api.LookupsView.as_view(), name="mobile-stocktake-lookups"),
    path("ghu11/scan/", stocktake_api.ScanView.as_view(), name="mobile-stocktake-scan"),
    path("ghu11/start/", stocktake_api.StartView.as_view(), name="mobile-stocktake-start"),
    path("ghu11/report/", stocktake_api.ReportView.as_view(), name="mobile-stocktake-report"),
]
