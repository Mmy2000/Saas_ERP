from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class RepairsConfig(AppConfig):
    name = "apps.repairs"
    label = "repairs"
    verbose_name = "Repairs"

    def ready(self):
        from apps.core.documents import register_document

        register_document("repairs.RepairOrder", "repair", _("Repair"))
