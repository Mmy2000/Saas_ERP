from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "apps.core"
    label = "core"

    def ready(self):
        from .numeric import configure_decimal_context

        configure_decimal_context()
