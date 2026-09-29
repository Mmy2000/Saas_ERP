from django.utils.translation import gettext_lazy as _

from apps.iam.catalog import register_permissions

register_permissions(_("Catalog"), [
    ("catalog.view", _("View karats, currencies and categories")),
    ("catalog.category.manage", _("Manage item categories")),
    ("catalog.karat.manage", _("Manage karats and fineness")),
])
