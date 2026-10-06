from django.apps import AppConfig


class JobsConfig(AppConfig):
    name = "apps.platform.jobs"
    label = "jobs"
    verbose_name = "Background jobs"

    def ready(self):
        from django.utils.module_loading import autodiscover_modules

        autodiscover_modules("jobs")  # each app's jobs.py registers its tasks and schedules
