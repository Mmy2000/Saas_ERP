from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ManufacturingConfig(AppConfig):
    name = "apps.manufacturing"
    label = "manufacturing"
    verbose_name = "Manufacturing"

    def ready(self):
        from apps.core.documents import register_document

        register_document("manufacturing.WorkOrder", "work-order", _("Work order"))
