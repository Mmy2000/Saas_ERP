"""Run background jobs (e-mails, daily reminders, clean-ups). Run it as a service next to the
app; start more than one for more throughput:

    python manage.py run_worker              # until stopped (Ctrl+C finishes the current job)
    python manage.py run_worker --once       # queue what is scheduled, run what is due, stop
"""

import signal
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections
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
        started = timezone.now()
        last_beat = last_tick = last_stale = 0.0
        done = failed = 0
        self.stdout.write(f"Worker {name} running. Ctrl+C to stop.")
        try:
            while not self._stop:
                close_old_connections()
                now = time.monotonic()
                if now - last_beat >= 15:
                    heartbeat(name, started, done=done, failed=failed)
                    done = failed = 0
                    last_beat = now
                if now - last_tick >= 30:
                    scheduler.tick()
                    last_tick = now
                if now - last_stale >= 300:
                    release_stale()
                    last_stale = now
                job = claim(name)
                if job is None:
                    time.sleep(poll)
                    continue
                if execute(job):
                    done += 1
                else:
                    failed += 1
        except KeyboardInterrupt:
            pass
        finally:
            Worker.objects.filter(name=name).delete()
            self.stdout.write("Stopped.")

    def _request_stop(self, *args):
        self._stop = True  # finish the current job, then stop
