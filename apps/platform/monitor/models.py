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


class ErrorEvent(models.Model):
    """A server error, one row per kind (same exception at the same place): how often, when
    first and last, and the last request it happened on. Written through its own connection
    (apps.platform.monitor.errors) so a failed request's rollback does not take it away."""

    fingerprint = models.CharField(max_length=64, unique=True)
    exc_type = models.CharField(max_length=200)
    message = models.CharField(max_length=500, blank=True)
    where = models.CharField(max_length=300, blank=True)  # file:line in function
    count = models.PositiveIntegerField(default=1)
    first_seen = models.DateTimeField()
    last_seen = models.DateTimeField(db_index=True)
    last_method = models.CharField(max_length=10, blank=True)
    last_path = models.CharField(max_length=500, blank=True)
    last_host = models.CharField(max_length=200, blank=True)
    last_tenant = models.ForeignKey("tenants.Tenant", null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")
    last_user = models.CharField(max_length=200, blank=True)
    traceback = models.TextField(blank=True)  # the last one
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-last_seen"]

    def __str__(self):
        return f"{self.exc_type}: {self.message[:60]}"


class BackupRun(models.Model):
    """One backup: the database dump and the uploaded files, written under BACKUP_DIR."""

    started_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)
    ok = models.BooleanField(default=False)
    database_file = models.CharField(max_length=300, blank=True)
    database_bytes = models.BigIntegerField(default=0)
    media_file = models.CharField(max_length=300, blank=True)
    media_bytes = models.BigIntegerField(default=0)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"backup {self.started_at:%Y-%m-%d %H:%M} {'ok' if self.ok else 'failed'}"
