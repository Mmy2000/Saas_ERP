"""Server samples for the platform console's monitoring page. Platform scope (no tenant, no RLS).
Readings are decimals, like every number in the project (§6.4).

Cumulative counters (bytes sent, read…) are stored as read from the operating system; the
rates are worked out against the previous sample when a sample is taken."""

from django.db import models


class SampleSource(models.TextChoices):
    COLLECTOR = "collector"  # manage.py monitor_server, running as a service
    PAGE = "page"  # taken while someone had the monitoring page open


class ServerSample(models.Model):
    taken_at = models.DateTimeField(db_index=True)
    source = models.CharField(max_length=10, choices=SampleSource.choices,
                              default=SampleSource.COLLECTOR)
    host = models.CharField(max_length=120, blank=True)

    cpu_pct = models.DecimalField(max_digits=5, decimal_places=1)
    load_1m = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    mem_pct = models.DecimalField(max_digits=5, decimal_places=1)
    mem_used = models.BigIntegerField()
    mem_total = models.BigIntegerField()
    swap_pct = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    disk_pct = models.DecimalField(max_digits=5, decimal_places=1)
    disk_used = models.BigIntegerField()
    disk_total = models.BigIntegerField()

    # Cumulative counters, and the per-second rates since the previous sample.
    net_sent = models.BigIntegerField(default=0)
    net_recv = models.BigIntegerField(default=0)
    io_read = models.BigIntegerField(default=0)
    io_write = models.BigIntegerField(default=0)
    net_out_bps = models.DecimalField(max_digits=20, decimal_places=1, null=True, blank=True)
    net_in_bps = models.DecimalField(max_digits=20, decimal_places=1, null=True, blank=True)
    io_read_bps = models.DecimalField(max_digits=20, decimal_places=1, null=True, blank=True)
    io_write_bps = models.DecimalField(max_digits=20, decimal_places=1, null=True, blank=True)

    process_rss = models.BigIntegerField(null=True, blank=True)  # the web process taking it
    db_size = models.BigIntegerField(null=True, blank=True)
    db_connections = models.IntegerField(null=True, blank=True)

    class Meta:
        ordering = ["-taken_at"]

    def __str__(self):
        return f"{self.taken_at:%Y-%m-%d %H:%M:%S} cpu {self.cpu_pct:.0f}%"
