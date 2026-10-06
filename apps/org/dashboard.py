"""Today's key figures for the dashboard (§17). Every block checks its own permission and is
limited to the branches the user may see; a block the user may not see is None."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Sum
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _

from apps.core.documents import document_titles, document_url
from apps.core.models import DocStatus
from apps.ledger.models import BalanceProjection, Commodity, JournalEntry

ZERO = Decimal(0)


def _scoped(queryset, branches, field_name="branch_id"):
    return queryset if branches is None else queryset.filter(**{f"{field_name}__in": branches})


@dataclass
class Amount:
    commodity: Commodity
    quantity: Decimal


def _amounts(rows) -> list[Amount]:
    """(commodity id, quantity) rows → labelled amounts, money first, zeroes dropped."""
    totals: dict[int, Decimal] = {}
    for commodity_id, quantity in rows:
        totals[commodity_id] = totals.get(commodity_id, ZERO) + (quantity or ZERO)
    commodities = Commodity.objects.select_related("metal", "currency").in_bulk(list(totals))
    amounts = [Amount(commodities[pk], q) for pk, q in totals.items() if q]
    return sorted(amounts, key=lambda a: (a.commodity.kind != "money", a.commodity.code))


def sales_today(actor) -> dict | None:
    if not actor.can("sales.invoice.view"):
        return None
    from apps.sales.models import SalesInvoice, SalesInvoiceLine

    today = timezone.localdate()
    branches = actor.branch_ids("sales.invoice.view")
    posted = _scoped(SalesInvoice.objects.filter(status=DocStatus.POSTED), branches)
    now = posted.filter(business_date=today).aggregate(n=Count("id"), total=Sum("total_amount"))
    before = posted.filter(business_date=today - timedelta(days=1)).aggregate(
        total=Sum("total_amount"))["total"] or ZERO
    weight = _scoped(SalesInvoiceLine.objects.filter(
        invoice__status=DocStatus.POSTED, invoice__business_date=today), branches,
        "invoice__branch_id").aggregate(g=Sum("gross_weight_g"))["g"] or ZERO
    total = now["total"] or ZERO
    change = (total - before) / before * 100 if before else None
    return {"count": now["n"], "total": total, "weight": weight, "yesterday": before,
            "change": change}


def sales_week(actor) -> list[dict] | None:
    """Retail sales per day for the last seven days, with a bar height for each."""
    if not actor.can("sales.invoice.view"):
        return None
    from apps.sales.models import SalesInvoice

    today = timezone.localdate()
    first = today - timedelta(days=6)
    totals = dict(_scoped(SalesInvoice.objects.filter(
        status=DocStatus.POSTED, business_date__gte=first, business_date__lte=today),
        actor.branch_ids("sales.invoice.view"))
        .values("business_date").annotate(t=Sum("total_amount"))
        .values_list("business_date", "t"))
    top = max(totals.values(), default=ZERO)
    days = []
    for offset in range(7):
        day = first + timedelta(days=offset)
        total = totals.get(day, ZERO)
        days.append({"day": day, "label": date_format(day, "D"), "total": total,
                     "height": int(total / top * 100) if top else 0, "today": day == today})
    return days


def wholesale_today(actor) -> dict | None:
    if not actor.can("sales.trade.view"):
        return None
    from apps.sales.models import TradeSale

    totals = _scoped(TradeSale.objects.filter(
        status=DocStatus.POSTED, business_date=timezone.localdate()),
        actor.branch_ids("sales.trade.view")).aggregate(
        n=Count("id"), fine=Sum("total_fine_weight_g"), money=Sum("money_amount"),
        gross=Sum("total_gross_weight_g"))
    return {"count": totals["n"], "fine": totals["fine"] or ZERO,
            "money": totals["money"] or ZERO, "gross": totals["gross"] or ZERO}


def cash_on_hand(actor) -> list[Amount] | None:
    if not actor.can("treasury.view"):
        return None
    from apps.treasury.models import CashBox

    branches = actor.branch_ids("treasury.view")
    accounts = _scoped(CashBox.objects.filter(is_active=True), branches).values("account_id")
    rows = BalanceProjection.objects.filter(account_id__in=accounts).values_list(
        "commodity_id").annotate(q=Sum("quantity"))
    return _amounts(rows)


def gold_stock(actor) -> dict | None:
    if not actor.can("inventory.stock.view"):
        return None
    from apps.inventory.selectors import stock_by_karat

    gold = [s for s in stock_by_karat(actor.branch_ids("inventory.stock.view"))
            if s.karat is not None and s.karat.metal.code == "gold"]
    return {"eq21": sum((s.equivalent_21k_g for s in gold), ZERO),
            "pieces": sum(s.pieces for s in gold)}


@dataclass
class Balances:
    label: str
    url: str
    hint: str
    amounts: list[Amount] = field(default_factory=list)
    deposits: list[Amount] = field(default_factory=list)


BALANCE_GROUPS = (
    # permission, account role, list url
    ("parties.customer.view", "customers", "customers"),
    ("parties.trade_account.view", "trade_accounts", "trade-accounts"),
    ("parties.supplier.view", "suppliers", "suppliers"),
    ("parties.workshop.view", "workshops", "workshops"),
)


def balances(actor) -> list[Balances] | None:
    """Net balances with customers, traders and suppliers per currency and metal."""
    labels = {
        "customers": (_("Customers"), _("Positive: they owe you.")),
        "trade_accounts": (_("Trade accounts"), _("Positive: they owe you.")),
        "suppliers": (_("Suppliers"), _("Negative: you owe them.")),
        "workshops": (_("Workshops"), _("Gold they hold for you; labour you owe.")),
    }
    groups = []
    for permission, role, url_name in BALANCE_GROUPS:
        if not actor.can(permission):
            continue
        rows = _scoped(BalanceProjection.objects.filter(account__role=role, party__isnull=False),
                       actor.branch_ids(permission)).values_list("commodity_id").annotate(
            q=Sum("quantity"))
        label, hint = labels[role]
        group = Balances(label, reverse(url_name), hint, _amounts(rows))
        if role == "customers":  # reservation deposits held for them
            group.deposits = _amounts(_scoped(BalanceProjection.objects.filter(
                account__role="customer_deposits", party__isnull=False),
                actor.branch_ids(permission)).values_list("commodity_id").annotate(
                q=Sum("quantity")))
        groups.append(group)
    return groups or None


@dataclass
class Attention:
    label: str
    count: int
    url: str
    icon: str


def needs_attention(actor) -> list[Attention]:
    today = timezone.localdate()
    items = []
    if actor.can("inventory.stock.view"):
        from apps.inventory.models import Stocktake, StockTransfer

        branches = actor.branch_ids("inventory.stock.view")
        incoming = _scoped(StockTransfer.objects.filter(
            status=DocStatus.POSTED, received_at__isnull=True), branches, "to_branch_id").count()
        if incoming:
            items.append(Attention(_("Transfers waiting to be received"), incoming,
                                   reverse("stock-transfers"), "truck"))
        counting = _scoped(Stocktake.objects.filter(status=DocStatus.DRAFT), branches).count()
        if counting:
            items.append(Attention(_("Stocktakes in progress"), counting,
                                   reverse("stocktakes"), "clipboard-check"))
    if actor.can("sales.reservation.view"):
        from apps.sales.models import Reservation

        overdue = _scoped(Reservation.objects.filter(
            status=DocStatus.POSTED, sale__isnull=True, expires_on__lt=today),
            actor.branch_ids("sales.reservation.view")).count()
        if overdue:
            items.append(Attention(_("Reservations past their date"), overdue,
                                   reverse("reservations"), "bookmark"))
    if actor.can("repairs.order.view"):
        from apps.repairs.models import RepairOrder

        open_repairs = _scoped(RepairOrder.objects.filter(
            status=DocStatus.POSTED, delivered_at__isnull=True),
            actor.branch_ids("repairs.order.view"))
        ready = open_repairs.filter(ready_on__isnull=False).count()
        if ready:
            items.append(Attention(_("Repairs ready for pickup"), ready,
                                   reverse("repairs") + "?state=ready", "wrench"))
        late = open_repairs.filter(promised_on__lt=today).count()
        if late:
            items.append(Attention(_("Repairs past their promised date"), late,
                                   reverse("repairs") + "?state=overdue", "clock"))
    if actor.can("manufacturing.order.view"):
        from apps.manufacturing.models import WorkOrder

        open_orders = _scoped(WorkOrder.objects.filter(
            status=DocStatus.POSTED, received_at__isnull=True),
            actor.branch_ids("manufacturing.order.view"))
        out = open_orders.filter(workshop__isnull=False).count()
        if out:
            items.append(Attention(_("Work orders at workshops"), out,
                                   reverse("work-orders"), "send"))
        making = open_orders.filter(workshop__isnull=True).count()
        if making:
            items.append(Attention(_("Production in progress"), making,
                                   reverse("production"), "flame"))
    if actor.can("purchasing.invoice.view"):
        from apps.purchasing.models import SupplierInvoice

        drafts = _scoped(SupplierInvoice.objects.filter(status=DocStatus.DRAFT),
                         actor.branch_ids("purchasing.invoice.view")).count()
        if drafts:
            items.append(Attention(_("Purchases not posted yet"), drafts,
                                   reverse("invoices") + "?status=draft", "receipt"))
    if actor.can("treasury.cheque.view"):
        from apps.treasury.models import OPEN_CHEQUE, Cheque, ChequeDirection

        open_cheques = _scoped(Cheque.objects.filter(status=DocStatus.POSTED,
                                                     state__in=OPEN_CHEQUE),
                               actor.branch_ids("treasury.cheque.view"))
        collect = open_cheques.filter(direction=ChequeDirection.RECEIVED,
                                      due_date__lte=today).count()
        if collect:
            items.append(Attention(_("Cheques due to collect"), collect,
                                   reverse("cheques") + "?direction=received&state=due",
                                   "receipt-text"))
        paying = open_cheques.filter(direction=ChequeDirection.ISSUED,
                                     due_date__lte=today + timedelta(days=7)).count()
        if paying:
            items.append(Attention(_("Issued cheques due this week"), paying,
                                   reverse("cheques") + "?direction=issued&state=open",
                                   "landmark"))
    if actor.can("ledger.period.close") and actor.branch_ids("ledger.period.close") is None:
        from apps.ledger.closing import next_to_close

        upcoming = next_to_close()
        if upcoming is not None:
            year, month = upcoming
            behind = (today.year - year) * 12 + today.month - month
            items.append(Attention(_("Finished months not closed yet"), behind,
                                   reverse("period", args=[year, month]), "calendar-check"))
    return items


def recent_documents(actor, limit: int = 8) -> list[dict] | None:
    """Today's latest postings, each linked to its document."""
    if not actor.can("ledger.view"):
        return None
    entries = list(_scoped(JournalEntry.objects.filter(
        business_date=timezone.localdate()).exclude(source_type=""),
        actor.branch_ids("ledger.view")).select_related("branch", "posted_by")
        .order_by("-posted_at", "-id")[:limit])
    titles = document_titles((e.source_type, e.source_id) for e in entries)
    documents = []
    for entry in entries:
        label, number = titles.get((entry.source_type, entry.source_id), (entry.memo, ""))
        documents.append({"label": label, "number": number or entry.number,
                          "url": document_url(entry.source_type, entry.source_id),
                          "at": entry.posted_at, "branch": entry.branch.name,
                          "by": getattr(entry.posted_by, "display_name", ""),
                          "reversal": entry.reverses_id is not None})
    return documents


def figures(actor) -> dict:
    return {
        "sales_today": sales_today(actor), "sales_week": sales_week(actor),
        "wholesale_today": wholesale_today(actor), "cash_on_hand": cash_on_hand(actor),
        "gold_stock": gold_stock(actor), "balances": balances(actor),
        "attention": needs_attention(actor), "recent": recent_documents(actor),
    }

