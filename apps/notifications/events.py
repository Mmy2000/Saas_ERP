"""The business events the bell reports, called from the services that make them happen (inside
their transaction) and from the morning job (apps/notifications/jobs.py)."""

from __future__ import annotations

from django.db.models import Count
from django.urls import reverse
from django.utils import timezone

from apps.core.models import DocStatus

from .services import notify, resolve


def stock_transfer_sent(transfer, *, by=None) -> None:
    notify("stock_transfer", key=f"stock_transfer:{transfer.pk}", branch=transfer.to_branch_id,
           url=reverse("stock-transfer", args=[transfer.pk]), by=by,
           params={"number": transfer.number, "branch": transfer.branch.name,
                   "count": int(transfer.total_qty),
                   "weight": str(transfer.total_gross_weight_g)})


def stock_transfer_done(transfer) -> None:
    resolve(f"stock_transfer:{transfer.pk}")


def cash_transfer_sent(doc, *, by=None) -> None:
    notify("cash_transfer", key=f"cash_transfer:{doc.pk}", branch=doc.to_branch_id,
           url=reverse("treasury-document", args=[doc.pk]), by=by,
           params={"number": doc.number, "branch": doc.branch.name, "amount": str(doc.amount),
                   "currency": doc.currency.code})


def cash_transfer_done(doc) -> None:
    resolve(f"cash_transfer:{doc.pk}")


def price_board_published(board, *, by=None) -> None:
    line = next((ln for ln in board.lines.all() if ln.karat_id == board.reference_karat_id),
                None)
    if line is not None and line.sell_price_per_g:
        notify("gold_price", key=f"gold_price:{board.pk}", url=reverse("gold-prices"), by=by,
               replaces="gold_price:",
               params={"karat": board.reference_karat.code,
                       "karat_name": board.reference_karat.display_name,
                       "sell": str(line.sell_price_per_g),
                       "buy": str(line.buy_price_per_g or "")})
    resolve_prefix("price_held:")


def price_held(jump_pct) -> None:
    notify("price_held", key=f"price_held:{timezone.localdate().isoformat()}",
           url=reverse("gold-prices"), params={"jump": str(jump_pct)})


def resolve_prefix(prefix: str) -> None:
    from .models import Notification

    Notification.objects.filter(key__startswith=prefix, read_at__isnull=True).update(
        read_at=timezone.now())


def _per_branch(kind: str, queryset, url: str, today) -> int:
    from apps.org.models import Branch

    told = 0
    counts = dict(queryset.values_list("branch_id").annotate(n=Count("id")).order_by())
    for branch in Branch.objects.filter(pk__in=counts):
        told += notify(kind, key=f"{kind}:{branch.pk}:{today.isoformat()}", branch=branch.pk,
                       url=url, params={"count": counts[branch.pk], "branch": branch.name})
    return told


def due_today(today=None) -> int:
    """The morning round: repairs promised, reservations ending and cheques due today."""
    from apps.repairs.models import RepairOrder
    from apps.sales.models import Reservation
    from apps.treasury.models import OPEN_CHEQUE, Cheque

    today = today or timezone.localdate()
    return (
        _per_branch("repairs_due", RepairOrder.objects.filter(
            status=DocStatus.POSTED, delivered_at__isnull=True, promised_on=today),
            reverse("repairs"), today)
        + _per_branch("reservations_due", Reservation.objects.filter(
            status=DocStatus.POSTED, sale__isnull=True, expires_on=today),
            reverse("reservations"), today)
        + _per_branch("cheques_due", Cheque.objects.filter(
            status=DocStatus.POSTED, state__in=OPEN_CHEQUE, due_date=today),
            reverse("cheques"), today))
