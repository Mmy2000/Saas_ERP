from django.utils.translation import gettext_lazy as _

from apps.iam.catalog import register_permissions

register_permissions(_("Customers"), [
    ("parties.customer.view", _("View customers")),
    ("parties.customer.create", _("Add customers")),
    ("parties.customer.edit", _("Edit customers")),
    ("parties.customer.deactivate", _("Deactivate customers")),
    ("parties.customer.view_pii", _("See customers' national IDs")),
])
register_permissions(_("Suppliers"), [
    ("parties.supplier.view", _("View suppliers")),
    ("parties.supplier.create", _("Add suppliers")),
    ("parties.supplier.edit", _("Edit suppliers")),
    ("parties.supplier.deactivate", _("Deactivate suppliers")),
])
register_permissions(_("Trade accounts"), [
    ("parties.trade_account.view", _("View trade accounts")),
    ("parties.trade_account.create", _("Add trade accounts")),
    ("parties.trade_account.edit", _("Edit trade accounts")),
    ("parties.trade_account.deactivate", _("Deactivate trade accounts")),
])
register_permissions(_("Workshops"), [
    ("parties.workshop.view", _("View workshops")),
    ("parties.workshop.create", _("Add workshops")),
    ("parties.workshop.edit", _("Edit workshops")),
    ("parties.workshop.deactivate", _("Deactivate workshops")),
])
