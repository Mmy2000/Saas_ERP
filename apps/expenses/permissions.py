from django.utils.translation import gettext_lazy as _

from apps.iam.catalog import register_permissions

register_permissions(_("Expenses"), [
    ("expenses.view", _("View expenses")),
    ("expenses.voucher.create", _("Record expenses")),
    ("expenses.void", _("Cancel expenses")),
    ("expenses.category.manage", _("Manage expense categories")),
])
