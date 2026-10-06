"""Template context for the application shell: navigation (filtered by permission) and the
tenant profile. Only computed on tenant hosts for signed-in members."""

from __future__ import annotations

from dataclasses import dataclass

from django.urls import reverse
from django.utils.functional import SimpleLazyObject
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _


@dataclass(frozen=True)
class NavItem:
    label: str
    url_name: str
    icon: str
    permission: str
    also_active_for: tuple[str, ...] = ()


NAVIGATION: list[tuple[str, list[NavItem]]] = [
    (_("Overview"), [
        NavItem(_("Dashboard"), "home", "layout-dashboard", "authenticated"),
    ]),
    (_("Sales"), [
        NavItem(_("New sale"), "sale-new", "scan-barcode", "sales.invoice.create"),
        NavItem(_("Sales"), "sales", "shopping-bag", "sales.invoice.view",
                ("sale-detail", "sale-return-detail")),
        NavItem(_("Reservations"), "reservations", "bookmark", "sales.reservation.view",
                ("reservation-new", "reservation")),
        NavItem(_("Wholesale"), "trade-sales", "scale", "sales.trade.view",
                ("trade-sale-new", "trade-sale", "trade-return")),
        NavItem(_("Repairs"), "repairs", "wrench", "repairs.order.view",
                ("repair-new", "repair")),
    ]),
    (_("Diamonds"), [
        NavItem(_("Diamonds and stones"), "diamonds", "gem", "diamonds.view"),
        NavItem(_("Receive diamonds"), "diamonds-receive-form", "inbox", "diamonds.receive"),
        NavItem(_("Stone setting"), "stone-settings", "sparkles", "diamonds.view",
                ("stone-setting-new", "stone-setting")),
    ]),
    (_("Pricing"), [
        NavItem(_("Gold prices"), "gold-prices", "coins", "pricing.board.view"),
        NavItem(_("Exchange rates"), "fx-rates", "arrow-left-right", "pricing.fx.view"),
    ]),
    (_("Goods"), [
        NavItem(_("Stock"), "stock", "package", "inventory.stock.view", ("stock-item",)),
        NavItem(_("Transfers"), "stock-transfers", "truck", "inventory.stock.view",
                ("stock-transfer-new", "stock-transfer")),
        NavItem(_("Stocktake"), "stocktakes", "clipboard-check", "inventory.stock.view",
                ("stocktake-new", "stocktake")),
        NavItem(_("Purchases"), "invoices", "receipt", "purchasing.invoice.view",
                ("invoice-new", "invoice-detail", "invoice-edit", "supplier-returns",
                 "supplier-return-new", "supplier-return")),
        NavItem(_("Scrap gold"), "scrap", "coins", "purchasing.scrap.view",
                ("scrap-buy", "scrap-sell", "scrap-purchase", "scrap-sale")),
        NavItem(_("Work orders"), "work-orders", "send", "manufacturing.order.view",
                ("work-order-new", "work-order")),
        NavItem(_("Production"), "production", "flame", "manufacturing.order.view",
                ("production-new", "production-order")),
    ]),
    (_("Partners"), [
        NavItem(_("Customers"), "customers", "users", "parties.customer.view",
                ("customer-new", "customer-edit", "customer-statement")),
        NavItem(_("Suppliers"), "suppliers", "truck", "parties.supplier.view",
                ("supplier-new", "supplier-edit", "supplier-statement")),
        NavItem(_("Trade accounts"), "trade-accounts", "building-2", "parties.trade_account.view",
                ("trade-account-new", "trade-account-edit", "trade-account-statement")),
        NavItem(_("Workshops"), "workshops", "store", "parties.workshop.view",
                ("workshop-new", "workshop-edit", "workshop-statement")),
        NavItem(_("Receipts & payments"), "settlements", "arrow-left-right", "settlements.view",
                ("settlement-new", "settlement-view")),
    ]),
    (_("Team"), [
        NavItem(_("Employees"), "employees", "users", "hr.employee.view",
                ("employee-new", "employee", "employee-advance")),
        NavItem(_("Payroll"), "payrolls", "wallet", "hr.payroll.view",
                ("payroll-new", "payroll")),
        NavItem(_("Commissions"), "commissions", "chart-column", "hr.payroll.view"),
    ]),
    (_("Treasury"), [
        NavItem(_("Cash & banks"), "treasury", "wallet", "treasury.view",
                ("treasury-documents", "treasury-document-new", "treasury-document",
                 "treasury-holder-new", "treasury-holder-edit", "treasury-holder-statement",
                 "reconcile")),
        NavItem(_("Cheques"), "cheques", "receipt-text", "treasury.cheque.view",
                ("cheque-new", "cheque")),
        NavItem(_("Cash counts"), "cash-counts", "calculator", "treasury.view",
                ("cash-count-new", "cash-count")),
        NavItem(_("Expenses"), "expenses", "receipt-text", "expenses.view",
                ("expense-new", "expense-categories", "expense-view")),
    ]),
    (_("Reports"), [
        NavItem(_("Reports"), "reports", "chart-column", "reports.daily_summary.view",
                ("report",)),
    ]),
    (_("Accounting"), [
        NavItem(_("Journal"), "journal", "book-open", "ledger.view", ("journal-new",)),
        NavItem(_("Profit and loss"), "profit-loss", "trending-up", "reports.profit_loss.view"),
        NavItem(_("Balance sheet"), "balance-sheet", "landmark", "reports.balance_sheet.view"),
        NavItem(_("Trial balance"), "trial-balance", "scale", "ledger.view"),
        NavItem(_("Chart of accounts"), "accounts", "list-tree", "ledger.view"),
        NavItem(_("Closing periods"), "periods", "calendar-check", "ledger.view", ("period",)),
    ]),
    (_("Catalog"), [
        NavItem(_("Item categories"), "categories", "layers", "catalog.view",
                ("category-new", "category-edit")),
        NavItem(_("Karats"), "karats", "gem", "catalog.view", ("karat-new", "karat-edit")),
    ]),
    (_("Settings"), [
        NavItem(_("Company"), "company", "settings", "org.settings.manage"),
        NavItem(_("Documents"), "document-designs", "printer", "org.settings.manage",
                ("document-design",)),
        NavItem(_("Branches"), "branches", "store", "org.branch.view",
                ("branch-new", "branch-edit")),
        NavItem(_("Users"), "users", "users", "admin.users.view", ("user-new", "user-edit")),
        NavItem(_("Roles"), "roles", "shield-check", "admin.users.view", ("role-new", "role-edit")),
        NavItem(_("Activity"), "activity", "history", "admin.audit.history"),
        NavItem(_("Label templates"), "label-templates", "printer", "printing.templates.manage",
                ("label-template-new", "label-template-edit")),
    ]),
]


def _navigation(request):
    actor = request.actor
    current = getattr(request.resolver_match, "url_name", None)
    sections = []
    for title, items in NAVIGATION:
        visible = [
            {"label": item.label, "url": reverse(item.url_name), "icon": item.icon,
             "active": current == item.url_name or current in item.also_active_for}
            for item in items if actor.can(item.permission)
        ]
        if visible:
            sections.append({"title": title, "items": visible})
    return sections


def shell(request):
    if getattr(request, "actor", None) is None:
        return {}
    from apps.org.models import TenantProfile

    return {
        "ui_strings": {
            "error": gettext("Please correct the highlighted fields."),
            "network": gettext("Could not reach the server. Check your connection and try again."),
            "search": gettext("Type to search…"),
            "no_results": gettext("No matches"),
            "loading": gettext("Searching…"),
            "clear": gettext("Clear"),
            "sale_done": gettext("Sale completed."),
        },
        "nav_sections": SimpleLazyObject(lambda: _navigation(request)),
        "tenant_profile": SimpleLazyObject(lambda: TenantProfile.objects.first()),
    }
