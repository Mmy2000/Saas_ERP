from django.utils.translation import gettext_lazy as _

from apps.diamonds import reports as diamond_reports  # noqa: F401, E402
from apps.iam.catalog import register_permissions

from . import (  # noqa: F401  (fill the registry)
    definitions,
    gold_reports,
    receivables,
    sales_reports,
    statements,
    visits,
)
from .registry import REPORTS

register_permissions(_("Reports"), [
    (report.permission(), report.title) for report in REPORTS.values()
])
