from django.utils.translation import gettext_lazy as _

from apps.iam.catalog import register_permissions

register_permissions(_("Stock"), [
    ("inventory.stock.view", _("View stock and pieces")),
    ("inventory.item.view_cost", _("See purchase costs")),
    ("inventory.item.print_label", _("Print piece labels")),
    ("inventory.transfer.send", _("Send goods to another branch")),
    ("inventory.transfer.receive", _("Receive goods from another branch")),
    ("inventory.transfer.void", _("Cancel transfers still in transit")),
    ("inventory.stocktake.start", _("Start and cancel stocktakes")),
    ("inventory.stocktake.count", _("Count pieces and weigh bulk stock in a stocktake")),
    ("inventory.stocktake.post", _("Post stocktakes (mark missing pieces, adjust bulk stock)")),
    ("inventory.blind_count.create", _("Do blind counts (pieces, gold weights and cash)")),
    ("inventory.blind_count.view", _("See blind counts and which figures differed")),
    # Owners by default ("admin."): the books' figures next to what was counted.
    ("admin.blind_count.reveal", _("See the real figures in blind counts")),
])
