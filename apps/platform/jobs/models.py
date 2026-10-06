"""The job queue: one table for every client, in PostgreSQL (no Redis or Celery).

A job is a row written in the same transaction as the work that asks for it (the outbox
pattern): if a sale is rolled back, its e-mail is never queued. `manage.py run_worker` claims
due rows with FOR UPDATE SKIP LOCKED, so several workers can run side by side, and runs each
client's job inside that client's tenant_context. Payloads hold ids, never personal data.
"""

from django.db import models
from django.db.models import Q
from django.utils import timezone


class JobStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    DONE = "done", "Done"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


class Job(models.Model):
    tenant = models.ForeignKey("tenants.Tenant", null=True, blank=True, on_delete=models.CASCADE,
                               related_name="+")  # None: a platform job
    name = models.CharField(max_length=100)  # a registered task (apps.platform.jobs.registry)
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=JobStatus.choices,
                              default=JobStatus.QUEUED)
    run_at = models.DateTimeField(default=timezone.now)
    attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=5)
    # Asked for twice with the same key (a daily reminder from two workers): kept once.
    dedupe_key = models.CharField(max_length=100, blank=True)
    last_error = models.TextField(blank=True)
    locked_by = models.CharField(max_length=120, blank=True)
    locked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["run_at", "id"], condition=Q(status="queued"),
                         name="jobs_job_due_idx"),
            models.Index(fields=["status", "created_at"], name="jobs_job_status_idx"),
            models.Index(fields=["tenant", "created_at"], name="jobs_job_tenant_idx"),
        ]
        constraints = [
            models.UniqueConstraint(fields=["name", "tenant", "dedupe_key"],
                                    condition=~Q(dedupe_key=""), nulls_distinct=False,
                                    name="jobs_job_dedupe_uniq"),
        ]

    def __str__(self):
        return f"{self.name} #{self.pk} ({self.status})"


class Worker(models.Model):
    """A running `run_worker` process, as last seen (the console shows whether one is alive)."""

    name = models.CharField(max_length=120, unique=True)  # host:pid
    started_at = models.DateTimeField()
    seen_at = models.DateTimeField()
    jobs_done = models.PositiveIntegerField(default=0)
    jobs_failed = models.PositiveIntegerField(default=0)

    def __str__(self):
        return self.name
