from django.utils.translation import gettext_lazy as _

from apps.iam.catalog import register_permissions

register_permissions(_("Accounting"), [
    ("ledger.view", _("View accounts, journal and trial balance")),
    ("ledger.journal.post", _("Post and reverse manual journal entries")),
    ("ledger.accounts.manage", _("Manage the chart of accounts")),
    ("ledger.period.close", _("Close months and financial years")),
    ("ledger.period.reopen", _("Reopen closed months and years")),
])
