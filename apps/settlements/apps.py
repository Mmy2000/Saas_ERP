from django.apps import AppConfig


class SettlementsConfig(AppConfig):
    name = "apps.settlements"
    label = "settlements"
    verbose_name = "Settlements"

    def ready(self):
        from apps.core.documents import register_document

        register_document("settlements.Settlement", "settlement-view",
                          lambda doc: doc.get_kind_display())
