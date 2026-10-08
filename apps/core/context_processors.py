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


@dataclass(frozen=True)
class NavSection:
    """A group of the sidebar: a header that opens and closes (remembered per user)."""

    key: str
    title: str
    icon: str
    items: list[NavItem]


NAV_COOKIE = "gweb_nav"  # the groups the user left open, comma separated
FLAT = {"overview"}  # shown as plain links, without a header

NAVIGATION: list[NavSection] = [
    NavSection("overview", _("Overview"), "layout-dashboard", [
        NavItem(_("Dashboard"), "home", "layout-dashboard", "authenticated"),
    ]),
    NavSection("sales", _("Sales"), "shopping-bag", [
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
    NavSection("diamonds", _("Diamonds"), "gem", [
        NavItem(_("Diamonds and stones"), "diamonds", "gem", "diamonds.view"),
        NavItem(_("Receive diamonds"), "diamonds-receive-form", "inbox", "diamonds.receive"),
        NavItem(_("Buy old diamonds"), "buy-backs", "coins", "diamonds.view",
                ("buy-back-new", "buy-back")),
        NavItem(_("Stone parcels"), "parcels", "layers", "diamonds.view", ("parcel",)),
        NavItem(_("Stones at workshops"), "workshop-stones", "hammer", "diamonds.view"),
        NavItem(_("Stone setting"), "stone-settings", "sparkles", "diamonds.view",
                ("stone-setting-new", "stone-setting")),
        NavItem(_("Setters"), "setting-jobs", "send", "diamonds.view",
                ("setting-job-new", "setting-job")),
        NavItem(_("Discount classes"), "discount-classes", "percent", "diamonds.view"),
        NavItem(_("Certificates"), "certificates", "shield-check", "diamonds.view"),
    ]),
    NavSection("pricing", _("Pricing"), "coins", [
        NavItem(_("Gold prices"), "gold-prices", "coins", "pricing.board.view"),
        NavItem(_("Exchange rates"), "fx-rates", "arrow-left-right", "pricing.fx.view"),
        NavItem(_("Price changes"), "price-changes", "pencil", "inventory.price.change",
                ("price-change",)),
        NavItem(_("Offers"), "offers", "sparkles", "sales.offer.view", ("offer-new", "offer")),
    ]),
    NavSection("goods", _("Goods"), "package", [
        NavItem(_("Stock"), "stock", "package", "inventory.stock.view", ("stock-item",)),
        NavItem(_("Transfers"), "stock-transfers", "truck", "inventory.stock.view",
                ("stock-transfer-new", "stock-transfer")),
        NavItem(_("In transit"), "in-transit", "arrow-left-right", "inventory.stock.view"),
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
    NavSection("parties", _("Customers and suppliers"), "users", [
        NavItem(_("Customers"), "customers", "users", "parties.customer.view",
                ("customer-new", "customer-edit", "customer-statement")),
        NavItem(_("Suppliers"), "suppliers", "truck", "parties.supplier.view",
                ("supplier-new", "supplier-edit", "supplier-statement")),
        NavItem(_("Trade accounts"), "trade-accounts", "building-2", "parties.trade_account.view",
                ("trade-account-new", "trade-account-edit", "trade-account-statement")),
        NavItem(_("Workshops"), "workshops", "store", "parties.workshop.view",
                ("workshop-new", "workshop-edit", "workshop-statement")),
        NavItem(_("Partners"), "partners", "key-round", "parties.partner.view",
                ("partner-new", "partner-edit", "partner-statement")),
        NavItem(_("Receipts & payments"), "settlements", "arrow-left-right", "settlements.view",
                ("settlement-new", "settlement-view")),
    ]),
    NavSection("team", _("Team"), "user-round", [
        NavItem(_("Employees"), "employees", "users", "hr.employee.view",
                ("employee-new", "employee", "employee-advance")),
        NavItem(_("Payroll"), "payrolls", "wallet", "hr.payroll.view",
                ("payroll-new", "payroll")),
        NavItem(_("Commissions"), "commissions", "chart-column", "hr.payroll.view"),
    ]),
    NavSection("treasury", _("Treasury"), "wallet", [
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
    NavSection("reports", _("Reports"), "chart-column", [
        NavItem(_("Reports"), "reports", "chart-column", "reports.daily_summary.view",
                ("report",)),
    ]),
    NavSection("accounting", _("Accounting"), "book-open", [
        NavItem(_("Journal"), "journal", "book-open", "ledger.view", ("journal-new",)),
        NavItem(_("Profit and loss"), "profit-loss", "trending-up", "reports.profit_loss.view"),
        NavItem(_("Balance sheet"), "balance-sheet", "landmark", "reports.balance_sheet.view"),
        NavItem(_("Trial balance"), "trial-balance", "scale", "ledger.view"),
        NavItem(_("Chart of accounts"), "accounts", "list-tree", "ledger.view"),
        NavItem(_("Closing periods"), "periods", "calendar-check", "ledger.view", ("period",)),
        NavItem(_("Opening balances"), "opening-balances", "inbox", "ledger.opening.view",
                ("opening-new", "opening-import", "opening-balance")),
    ]),
    NavSection("catalog", _("Catalog"), "layers", [
        NavItem(_("Item categories"), "categories", "layers", "catalog.view",
                ("category-new", "category-edit")),
        NavItem(_("Karats"), "karats", "gem", "catalog.view", ("karat-new", "karat-edit")),
    ]),
    NavSection("settings", _("Settings"), "settings", [
        NavItem(_("Company"), "company", "settings", "org.settings.manage"),
        NavItem(_("Documents"), "document-designs", "printer", "org.settings.manage",
                ("document-design",)),
        NavItem(_("Branches"), "branches", "store", "org.branch.view",
                ("branch-new", "branch-edit")),
        NavItem(_("Users"), "users", "users", "admin.users.view", ("user-new", "user-edit")),
        NavItem(_("Roles"), "roles", "shield-check", "admin.users.view", ("role-new", "role-edit")),
        NavItem(_("Activity"), "activity", "history", "admin.audit.history"),
        NavItem(_("Sign-ins"), "sign-ins", "log-in", "admin.signins.history"),
        NavItem(_("Sent messages"), "messages", "send", "messaging.view"),
        NavItem(_("Sale codes"), "sale-code-settings", "shield-check", "sales.code.manage"),
        NavItem(_("Label templates"), "label-templates", "printer", "printing.templates.manage",
                ("label-template-new", "label-template-edit")),
    ]),
]


def _navigation(request):
    actor = request.actor
    current = getattr(request.resolver_match, "url_name", None)
    remembered = request.COOKIES.get(NAV_COOKIE)
    opened = set(remembered.split(",")) if remembered else set()
    sections = []
    for section in NAVIGATION:
        visible = [
            {"label": item.label, "url": reverse(item.url_name), "icon": item.icon,
             "active": current == item.url_name or current in item.also_active_for}
            for item in section.items if actor.can(item.permission)
        ]
        if not visible:
            continue
        holds_current = any(item["active"] for item in visible)
        sections.append({"key": section.key, "title": section.title, "icon": section.icon,
                         # A group of one screen is just a link to it.
                         "items": visible, "flat": section.key in FLAT or len(visible) == 1,
                         "current": holds_current,
                         # The current page's group is always open; others as the user left them.
                         "open": holds_current or section.key in opened})
    return sections


def _bell(request) -> dict:
    from apps.notifications.kinds import KINDS
    from apps.notifications.services import unread_count

    unread = unread_count(request.actor)
    # Every icon the bell's menu may draw, for its script (notification kinds and attention).
    icons = sorted({"bell", "clock", "send", "flame", "receipt", "landmark", "calendar-check",
                    "clipboard-check", *(kind.icon for kind in KINDS.values())})
    return {"unread": unread, "label": "99+" if unread > 99 else str(unread), "icons": icons}


def _shortcuts(request) -> dict:
    """The member's pinned screens, and whether the screen they are on can be (or is) pinned."""
    from apps.iam.shortcuts import pinned, screens

    current = getattr(request.resolver_match, "url_name", None)
    mine = pinned(request.actor)
    item = screens().get(current)
    screen = None
    if item is not None and request.actor.can(item.permission):
        screen = {"name": current, "label": str(item.label),
                  "pinned": any(s["screen"] == current for s in mine)}
    return {"items": mine, "screen": screen}


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
            "page_loading": gettext("Loading…"),
            "pinned": gettext("Pinned to your shortcuts."),
            "unpinned": gettext("Removed from your shortcuts."),
        },
        "nav_sections": SimpleLazyObject(lambda: _navigation(request)),
        "bell": SimpleLazyObject(lambda: _bell(request)),
        "shortcuts": SimpleLazyObject(lambda: _shortcuts(request)),
        "tenant_profile": SimpleLazyObject(lambda: TenantProfile.objects.first()),
    }
