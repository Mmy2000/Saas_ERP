"""The morning round of reminders, and clearing out old notifications."""

from datetime import time

from django.utils.translation import gettext_lazy as _

from apps.platform.jobs.registry import daily, task


@task("notifications.morning", label=_("Morning reminders (repairs, reservations, cheques)"),
      max_attempts=3)
def morning():
    from .events import due_today
    from .services import clean_up

    due_today()
    clean_up()


daily("notifications.morning", at=time(8, 0))
