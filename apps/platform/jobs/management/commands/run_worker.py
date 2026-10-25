"""Run background jobs (e-mails, daily reminders, clean-ups). Run it as a service next to the
app; start more than one for more throughput:

    python manage.py run_worker              # until stopped (Ctrl+C finishes the current job)
    python manage.py run_worker --once       # queue what is scheduled, run what is due, stop
"""

import logging
import signal
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections, connection
from django.utils import timezone

from apps.platform.jobs.models import Worker
from apps.platform.jobs.worker import (
    Scheduler,
    claim,
    execute,
    heartbeat,
    release_stale,
    run_pending,
    worker_name,
)

logger = logging.getLogger(__name__)
ERROR_PAUSE = 5  # seconds before trying again after a pass failed (the database restarting…)


class Command(BaseCommand):
    help = "Run queued background jobs and the daily schedules."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true",
                            help="Queue scheduled jobs, run every due job, then stop.")
        parser.add_argument("--sleep", type=float, default=None,
                            help="Seconds to wait when the queue is empty (default "
                                 "JOBS_POLL_SECONDS).")

    def handle(self, *args, once=False, sleep=None, **options):
        name = worker_name()
        scheduler = Scheduler()
        if once:
            queued = scheduler.tick()
            release_stale()
            ran = run_pending(name=name)
            self.stdout.write(f"Queued {queued} scheduled job(s), ran {ran}.")
            return

        poll = sleep or settings.JOBS_POLL_SECONDS
        self._stop = False
        signal.signal(signal.SIGTERM, self._request_stop)
        self._started = timezone.now()
        self._last = {"beat": 0.0, "tick": 0.0, "stale": 0.0}  # monotonic time of each chore
        self._done = self._failed = 0
        self.stdout.write(f"Worker {name} running. Ctrl+C to stop.")
        try:
            while not self._stop:
                # One pass failing (the database restarting, a lost connection) must not stop
                # the worker: nothing would send, schedule or back up until someone noticed.
                try:
                    self._pass(name, scheduler, poll)
                except Exception:  # noqa: BLE001 - logged (Console → Errors), then retried
                    logger.exception("Worker %s: a pass failed; trying again in %s s", name,
                                     ERROR_PAUSE)
                    connection.close()  # a broken connection is opened again next pass
                    time.sleep(ERROR_PAUSE)
        except KeyboardInterrupt:
            pass
        finally:
            try:
                Worker.objects.filter(name=name).delete()
            except Exception:  # noqa: BLE001 - stopping anyway; the row just ages out
                logger.warning("Worker %s: could not remove its heartbeat row", name)
            self.stdout.write("Stopped.")

    def _pass(self, name: str, scheduler: Scheduler, poll: float) -> None:
        """The chores that are due (heartbeat every 15 s, schedules every 30 s, abandoned jobs
        every 5 min: each one's clock moves only once it succeeded), then one job, or a wait."""
        close_old_connections()
        now = time.monotonic()
        if now - self._last["beat"] >= 15:
            heartbeat(name, self._started, done=self._done, failed=self._failed)
            self._done = self._failed = 0
            self._last["beat"] = now
        if now - self._last["tick"] >= 30:
            scheduler.tick()
            self._last["tick"] = now
        if now - self._last["stale"] >= 300:
            release_stale()
            self._last["stale"] = now
        job = claim(name)
        if job is None:
            time.sleep(poll)
        elif execute(job):
            self._done += 1
        else:
            self._failed += 1

    def _request_stop(self, *args):
        self._stop = True  # finish the current job, then stop
