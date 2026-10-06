"""Record server samples for the console's Server page. Run it as a service next to the app:

    python manage.py monitor_server            # every MONITOR_SAMPLE_SECONDS, until stopped
    python manage.py monitor_server --once     # one sample (e.g. from cron)
"""

import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections

from apps.platform.monitor.models import SampleSource
from apps.platform.monitor.sampler import prune, take_sample


class Command(BaseCommand):
    help = "Record CPU, memory, disk, network and database samples for the platform console."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Take one sample and stop.")
        parser.add_argument("--interval", type=int, default=None,
                            help="Seconds between samples (default MONITOR_SAMPLE_SECONDS).")

    def handle(self, *args, once=False, interval=None, **options):
        interval = interval or settings.MONITOR_SAMPLE_SECONDS
        if once:
            sample = take_sample(SampleSource.COLLECTOR)
            prune()
            self.stdout.write(str(sample))
            return
        self.stdout.write(f"Recording a sample every {interval} s. Ctrl+C to stop.")
        pruned_at = 0.0
        try:
            while True:
                started = time.monotonic()
                close_old_connections()
                take_sample(SampleSource.COLLECTOR)
                if started - pruned_at > 3600:
                    prune()
                    pruned_at = started
                time.sleep(max(0.0, interval - (time.monotonic() - started)))
        except KeyboardInterrupt:
            self.stdout.write("Stopped.")
