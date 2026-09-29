from django.utils.translation import gettext_lazy as _

from apps.iam.catalog import register_permissions

from . import definitions  # noqa: F401  (fills the registry)
from .registry import REPORTS

register_permissions(_("Reports"), [
    (report.permission(), report.title) for report in REPORTS.values()
])
