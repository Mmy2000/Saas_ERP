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
])
