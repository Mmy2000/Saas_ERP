from django.apps import AppConfig


class TreasuryConfig(AppConfig):
    name = "apps.treasury"
    label = "treasury"
    verbose_name = "Treasury"

    def ready(self):
        from apps.core.documents import register_document

        register_document("treasury.TreasuryDocument", "treasury-document",
                          lambda doc: doc.title)
