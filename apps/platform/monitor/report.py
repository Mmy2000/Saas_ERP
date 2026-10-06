"""History for the Server page: samples averaged into buckets, laid out as SVG line charts.

The charts are drawn server-side in a 0..WIDTH × 0..HEIGHT viewBox; lines keep a constant 2px
stroke (vector-effect) however the chart is stretched. Empty buckets break the line."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from django.conf import settings
from django.db import connection
from django.template.defaultfilters import filesizeformat
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from .models import SampleSource, ServerSample

WIDTH, HEIGHT = 600, 160
# key: (label, length, bucket seconds)
WINDOWS = {
    "15m": (gettext_lazy("15 minutes"), timedelta(minutes=15), 15),
    "1h": (gettext_lazy("1 hour"), timedelta(hours=1), 60),
    "24h": (gettext_lazy("24 hours"), timedelta(hours=24), 20 * 60),
    "7d": (gettext_lazy("7 days"), timedelta(days=7), 2 * 3600),
}
DEFAULT_WINDOW = "1h"


def window(key: str) -> str:
    return key if key in WINDOWS else DEFAULT_WINDOW


def level(percent: float | None) -> str:
    """good / warning / critical against the configured thresholds."""
    if percent is None:
        return "unknown"
    if percent >= settings.MONITOR_CRITICAL_PCT:
        return "critical"
    return "warning" if percent >= settings.MONITOR_WARN_PCT else "good"


@dataclass
class Series:
    key: str  # css slot: series-1, series-2
    label: str
    values: list[float | None]
    line: str = ""
    area: str = ""
    latest: str = ""  # newest value, formatted


@dataclass
class Chart:
    title: str
    unit: str  # "pct" or "bytes"
    series: list[Series]
    times: list[datetime]
    top: float  # the value at the top of the chart
    ticks: list[tuple[float, str]] = field(default_factory=list)  # (y, label)
    latest: list[str] = field(default_factory=list)  # newest value per series, formatted
    peak: str = ""

    @property
    def empty(self) -> bool:
        return not any(v is not None for s in self.series for v in s.values)

    @property
    def points(self) -> str:
        """Tooltip data for static/core/js/charts.js: per bucket, its time and the values."""
        rows = []
        for index, moment in enumerate(self.times):
            rows.append([date_format(timezone.localtime(moment), "SHORT_DATETIME_FORMAT"),
                         *[_format(s.values[index], self.unit) for s in self.series]])
        return json.dumps({"labels": [s.label for s in self.series], "rows": rows},
                          ensure_ascii=False)


def _format(value: float | None, unit: str) -> str:
    if value is None:
        return "—"
    if unit == "pct":
        return f"{value:.0f}%"
    return per_second(value)


def per_second(value: float) -> str:
    return _("%(size)s/s") % {"size": filesizeformat(value)}


def _nice_top(maximum: float) -> float:
    if maximum <= 0:
        return 1024.0
    magnitude = 1.0
    while magnitude * 10 <= maximum:
        magnitude *= 10
    for step in (1, 2, 2.5, 5, 10):
        if step * magnitude >= maximum:
            return step * magnitude
    return 10 * magnitude


def _paths(values: list[float | None], top: float) -> tuple[str, str]:
    count = len(values)
    if count < 2 or top <= 0:
        return "", ""
    step = WIDTH / (count - 1)
    line, area, run = [], [], []

    def close_run():
        if len(run) >= 1:
            x0, x1 = run[0][0], run[-1][0]
            area.append(f"M{x0:.1f},{HEIGHT} " + " ".join(f"L{x:.1f},{y:.1f}" for x, y in run)
                        + f" L{x1:.1f},{HEIGHT} Z")

    for index, value in enumerate(values):
        if value is None:
            close_run()
            run = []
            continue
        x, y = index * step, HEIGHT - min(value, top) / top * HEIGHT
        line.append(("M" if not run else "L") + f"{x:.1f},{y:.1f}")
        run.append((x, y))
    close_run()
    return " ".join(line), " ".join(area)


@dataclass
class History:
    key: str
    label: str
    cpu: Chart
    memory: Chart
    network: Chart
    disk_io: Chart
    cpu_avg: float | None
    cpu_peak: float | None
    mem_peak: float | None
    samples: int
    collector_seen: datetime | None  # the last sample the background collector took

    @property
    def collector_running(self) -> bool:
        return (self.collector_seen is not None and timezone.now() - self.collector_seen
                < timedelta(seconds=settings.MONITOR_SAMPLE_SECONDS * 6))


def _buckets(start: datetime, bucket: int) -> dict[int, dict]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT floor(extract(epoch FROM taken_at) / %s)::bigint AS b,
                   avg(cpu_pct), max(cpu_pct), avg(mem_pct), max(mem_pct),
                   avg(net_in_bps), avg(net_out_bps), avg(io_read_bps), avg(io_write_bps)
            FROM monitor_serversample WHERE taken_at >= %s GROUP BY b ORDER BY b
            """, [bucket, start])
        names = ("cpu", "cpu_max", "mem", "mem_max", "net_in", "net_out", "io_read", "io_write")
        return {row[0]: dict(zip(names, row[1:], strict=True)) for row in cursor.fetchall()}


def _percent_chart(title, key, label, values, times) -> Chart:
    line, area = _paths(values, 100)
    latest = next((v for v in reversed(values) if v is not None), None)
    peak = max((v for v in values if v is not None), default=None)
    return Chart(title=title, unit="pct", times=times, top=100,
                 series=[Series(key, label, values, line, area)],
                 ticks=[(HEIGHT - p / 100 * HEIGHT, f"{p}%") for p in (0, 25, 50, 75, 100)],
                 latest=[_format(latest, "pct")],
                 peak=_format(peak, "pct") if peak is not None else "—")


def _rate_chart(title, series, times) -> Chart:
    top = _nice_top(max((v for _k, _l, values in series for v in values if v is not None),
                        default=0))
    built = []
    for key, label, values in series:
        line, area = _paths(values, top)
        latest = next((v for v in reversed(values) if v is not None), None)
        built.append(Series(key, label, values, line, area, _format(latest, "bytes")))
    return Chart(title=title, unit="bytes", times=times, top=top, series=built,
                 ticks=[(HEIGHT - f * HEIGHT, per_second(top * f))
                        for f in (0, 0.5, 1)],
                 latest=[_format(next((v for v in reversed(values) if v is not None), None),
                                 "bytes") for _k, _l, values in series])


def history(key: str) -> History:
    key = window(key)
    label, length, bucket = WINDOWS[key]
    now = timezone.now()
    first = int((now - length).timestamp() // bucket) + 1
    last = int(now.timestamp() // bucket)
    rows = _buckets(datetime.fromtimestamp(first * bucket, tz=UTC), bucket)
    numbers = list(range(first, last + 1))
    times = [datetime.fromtimestamp(n * bucket, tz=UTC) for n in numbers]

    def column(name):  # floats from here on: they only position the drawing
        return [float(rows[n][name]) if n in rows and rows[n][name] is not None else None
                for n in numbers]

    cpu, memory = column("cpu"), column("mem")
    cpu_max = [v for v in column("cpu_max") if v is not None]
    mem_max = [v for v in column("mem_max") if v is not None]
    present = [v for v in cpu if v is not None]
    window_start = now - length
    return History(
        key=key, label=str(label),
        cpu=_percent_chart(_("CPU"), "series-1", _("CPU"), cpu, times),
        memory=_percent_chart(_("Memory"), "series-1", _("Memory"), memory, times),
        network=_rate_chart(_("Network"), [("series-1", _("Incoming"), column("net_in")),
                                           ("series-2", _("Outgoing"), column("net_out"))], times),
        disk_io=_rate_chart(_("Disk activity"), [("series-1", _("Read"), column("io_read")),
                                                 ("series-2", _("Written"),
                                                  column("io_write"))], times),
        cpu_avg=sum(present) / len(present) if present else None,
        cpu_peak=max(cpu_max, default=None), mem_peak=max(mem_max, default=None),
        samples=ServerSample.objects.filter(taken_at__gte=window_start).count(),
        collector_seen=ServerSample.objects.filter(source=SampleSource.COLLECTOR)
        .values_list("taken_at", flat=True).first(),
    )
