"""Server monitoring in the platform console."""

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.test import Client, override_settings
from django.utils import timezone

from apps.platform.monitor import report, sampler
from apps.platform.monitor.models import SampleSource, ServerSample
from tests.test_console import HOST, console, staff  # noqa: F401 - fixtures

pytestmark = pytest.mark.django_db


def test_samples_and_rates():
    first = sampler.take_sample()
    assert 0 <= first.cpu_pct <= 100 and 0 < first.mem_pct <= 100
    assert first.mem_total > 0 and first.disk_total > 0
    assert first.db_size and first.db_size > 0 and first.db_connections >= 1
    assert first.net_in_bps is None  # nothing to compare with yet
    ServerSample.objects.filter(pk=first.pk).update(taken_at=timezone.now() - timedelta(seconds=10))
    second = sampler.take_sample()
    assert second.net_in_bps is not None and second.net_in_bps >= 0
    assert second.io_read_bps is not None


@override_settings(MONITOR_SAMPLE_SECONDS=60, MONITOR_RETENTION_DAYS=7)
def test_page_samples_are_throttled_and_old_ones_pruned():
    assert sampler.sample_if_due() is not None
    assert sampler.sample_if_due() is None  # one per MONITOR_SAMPLE_SECONDS
    old = sampler.take_sample()
    ServerSample.objects.filter(pk=old.pk).update(taken_at=timezone.now() - timedelta(days=8))
    assert sampler.prune() == 1
    assert ServerSample.objects.count() == 1
    assert ServerSample.objects.get().source == SampleSource.PAGE


@override_settings(MONITOR_WARN_PCT=75, MONITOR_CRITICAL_PCT=90)
def test_history_and_levels():
    for minutes, cpu in ((50, 20.0), (30, 60.0), (10, 95.0)):
        sample = sampler.take_sample()
        ServerSample.objects.filter(pk=sample.pk).update(
            taken_at=timezone.now() - timedelta(minutes=minutes), cpu_pct=cpu)
    history = report.history("1h")
    assert history.key == "1h" and history.samples == 3
    assert history.cpu_peak == 95.0 and round(history.cpu_avg) == 58
    assert history.cpu.series[0].line.startswith("M") and not history.cpu.empty
    assert history.cpu.peak == "95%"
    assert not history.collector_running  # none of these is recent
    assert len(history.cpu.times) == 60  # one-minute buckets
    assert report.history("nonsense").key == report.DEFAULT_WINDOW
    assert [report.level(v) for v in (10, 80, 95, None)] == [
        "good", "warning", "critical", "unknown"]


def test_collector_command():
    call_command("monitor_server", "--once")
    sample = ServerSample.objects.get()
    assert sample.source == SampleSource.COLLECTOR
    assert report.history("15m").collector_running


def test_console_page_and_live_parts(console):  # noqa: F811
    page = console.get("/server/?window=15m")
    assert page.status_code == 200
    html = page.content.decode()
    assert "Busiest processes" in html and "data-chart" in html and "PostgreSQL" in html
    live = console.get("/server/live/?window=24h")
    assert live.status_code == 200
    assert set(live.json()["parts"]) == {"tiles", "charts", "disks", "processes", "info"}
    assert live.json()["window"] == "24h"
    assert ServerSample.objects.exists()  # the page records while it is open


def test_only_staff(tenant_a):
    client = Client(HTTP_HOST=HOST)
    assert client.get("/server/").status_code == 302
    assert client.get("/server/live/").status_code == 302
