from django.utils.translation import gettext_lazy as _

from apps.iam.catalog import register_limit, register_permissions

register_permissions(_("Sales"), [
    ("sales.invoice.view", _("View sales")),
    ("sales.invoice.create", _("Sell (create and post sales invoices)")),
    ("sales.invoice.void", _("Cancel today's sales")),
    ("sales.invoice.void_any", _("Cancel sales from earlier days")),
    ("sales.trade_in.override_price", _("Change the scrap gold price on a trade-in")),
    ("sales.return.create", _("Take customer returns")),
    ("sales.return.void", _("Cancel customer returns")),
    ("sales.reservation.view", _("View reservations")),
    ("sales.reservation.create", _("Reserve pieces for customers and take deposits")),
    ("sales.reservation.complete", _("Complete reservations into sales")),
    ("sales.reservation.cancel", _("Cancel reservations and refund deposits")),
])

# Legacy `User.pr_gold`: the largest discount a seller may give on the making charge.
register_limit("sales.discount.max_rate.gold", _("Maximum making-charge discount (gold)"), "0")

register_permissions(_("Wholesale"), [
    ("sales.trade.view", _("View wholesale sales and returns")),
    ("sales.trade.create", _("Sell wholesale to trade accounts")),
    ("sales.trade.void", _("Cancel wholesale sales")),
    ("sales.trade.return", _("Take back wholesale goods and cancel those returns")),
])
