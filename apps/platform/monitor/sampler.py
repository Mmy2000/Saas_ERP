"""Reading the server: samples for history (stored) and a live snapshot (not stored)."""

from __future__ import annotations

import functools
import os
import platform
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

import django
import psutil
from django.conf import settings
from django.db import connection
from django.utils import timezone

from .models import SampleSource, ServerSample

_lock = threading.Lock()  # one page-triggered sample at a time; the process cache
_cpu_lock = threading.Lock()  # separate: taking a sample (under _lock) reads the CPU too
_processes: dict[int, psutil.Process] = {}  # kept between calls so their CPU % can be measured
_cpu_reading: dict = {"at": 0.0, "total": 0.0, "per_core": []}


def _cached(seconds: float | None):
    """Keep a slow reading for `seconds` (None: for the life of the process). Process lists and
    database sizes are slow on some systems and need not be re-read every few seconds."""
    def wrap(function):
        store: dict = {}
        guard = threading.Lock()

        @functools.wraps(function)
        def cached():
            with guard:
                now = time.monotonic()
                if "value" not in store or (seconds is not None and now - store["at"] >= seconds):
                    store.update(value=function(), at=now)
                return store["value"]
        cached.clear = store.clear
        return cached
    return wrap


def _busy_pct(before, after) -> float:
    """Busy share of one CPU between two cpu_times() readings, in %."""
    total = sum(after) - sum(before)
    if total <= 0:
        return 0.0
    idle = (after.idle - before.idle) + (getattr(after, "iowait", 0) - getattr(before, "iowait", 0))
    return max(0.0, min(100.0, round((1 - idle / total) * 100, 1)))


def cpu_now() -> tuple[float, list[float]]:
    """Total and per-thread CPU %, from the CPU times since the previous reading (computed
    here rather than with psutil.cpu_percent, whose shared state misreads the first call in a
    threaded server). The first reading measures 0.3 s; readings closer than a second apart
    share the last one. The total is the threads' average."""
    with _cpu_lock:
        now = time.monotonic()
        if _cpu_reading["per_core"] and now - _cpu_reading["at"] < 1.0:
            return _cpu_reading["total"], list(_cpu_reading["per_core"])
        before = _cpu_reading.get("times")
        if before is None:
            before = psutil.cpu_times(percpu=True)
            time.sleep(0.3)
        after = psutil.cpu_times(percpu=True)
        per_core = [_busy_pct(a, b) for a, b in zip(before, after, strict=False)]
        total = round(sum(per_core) / len(per_core), 1) if per_core else 0.0
        _cpu_reading.update(at=now, total=total, per_core=per_core, times=after)
        return total, list(per_core)


def app_disk() -> str:
    """The disk the application (and its uploads) lives on."""
    return os.path.splitdrive(str(settings.BASE_DIR))[0] + os.sep if os.name == "nt" else "/"


@_cached(30)
def _database() -> tuple[int | None, int | None]:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_database_size(current_database()), "
                           "(SELECT count(*) FROM pg_stat_activity "
                           " WHERE datname = current_database())")
            size, connections = cursor.fetchone()
            return size, connections
    except Exception:  # noqa: BLE001 - monitoring must never break the page
        return None, None


def _dec(value: float | None, places: str = "0.1") -> Decimal | None:
    return None if value is None else Decimal(str(value)).quantize(Decimal(places))


def _rate(now: int, before: int, seconds: float) -> Decimal | None:
    if seconds <= 0 or now < before:  # a counter reset (reboot, new interface)
        return None
    return _dec((now - before) / seconds)


def take_sample(source: str = SampleSource.COLLECTOR) -> ServerSample:
    now = timezone.now()
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage(app_disk())
    net = psutil.net_io_counters()
    io = psutil.disk_io_counters()
    try:
        load = psutil.getloadavg()[0] if os.name != "nt" else None
    except (AttributeError, OSError):
        load = None
    db_size, db_connections = _database()
    sample = ServerSample(
        taken_at=now, source=source, host=socket.gethostname()[:120],
        cpu_pct=_dec(cpu_now()[0]), load_1m=_dec(load, "0.01"),
        mem_pct=_dec(memory.percent), mem_used=memory.total - memory.available,
        mem_total=memory.total, swap_pct=_dec(psutil.swap_memory().percent),
        disk_pct=_dec(disk.percent), disk_used=disk.used, disk_total=disk.total,
        net_sent=net.bytes_sent if net else 0, net_recv=net.bytes_recv if net else 0,
        io_read=io.read_bytes if io else 0, io_write=io.write_bytes if io else 0,
        process_rss=psutil.Process().memory_info().rss,
        db_size=db_size, db_connections=db_connections,
    )
    previous = ServerSample.objects.filter(taken_at__lt=now).first()
    if previous is not None:
        seconds = (now - previous.taken_at).total_seconds()
        if seconds <= 600:  # rates over a long gap would mislead
            sample.net_out_bps = _rate(sample.net_sent, previous.net_sent, seconds)
            sample.net_in_bps = _rate(sample.net_recv, previous.net_recv, seconds)
            sample.io_read_bps = _rate(sample.io_read, previous.io_read, seconds)
            sample.io_write_bps = _rate(sample.io_write, previous.io_write, seconds)
    sample.save()
    return sample


def prune() -> int:
    cutoff = timezone.now() - timedelta(days=settings.MONITOR_RETENTION_DAYS)
    deleted, _ = ServerSample.objects.filter(taken_at__lt=cutoff).delete()
    return deleted


def sample_if_due(source: str = SampleSource.PAGE) -> ServerSample | None:
    """Take a sample unless one was taken in the last MONITOR_SAMPLE_SECONDS (several
    console tabs or workers polling at once still give one sample)."""
    with _lock:
        since = timezone.now() - timedelta(seconds=settings.MONITOR_SAMPLE_SECONDS)
        if ServerSample.objects.filter(taken_at__gte=since).exists():
            return None
        sample = take_sample(source)
        prune()
        return sample


# --- the live snapshot ---------------------------------------------------------------------------

@dataclass
class Disk:
    mount: str
    fstype: str
    used: int
    total: int
    percent: float
    is_app: bool = False


@dataclass
class Proc:
    pid: int
    name: str
    cpu: float
    rss: int
    is_self: bool = False


@dataclass
class Snapshot:
    taken_at: datetime
    host: str
    os: str
    cpu_model: str
    cores: int
    threads: int
    cpu_pct: float
    per_core: list[float]
    freq_mhz: float | None
    load: tuple[float, float, float] | None
    mem_total: int
    mem_used: int
    mem_available: int
    mem_pct: float
    swap_total: int
    swap_used: int
    swap_pct: float
    booted_at: datetime
    python: str
    django: str
    postgres: str
    db_size: int | None
    db_connections: int | None
    process_rss: int
    process_cpu: float
    process_threads: int
    process_started: datetime
    disks: list[Disk] = field(default_factory=list)
    processes: list[Proc] = field(default_factory=list)

    @property
    def uptime(self) -> timedelta:
        return self.taken_at - self.booted_at

    @property
    def process_uptime(self) -> timedelta:
        return self.taken_at - self.process_started


@_cached(None)
def _cpu_model() -> str:
    name = platform.processor() or platform.machine()
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/cpuinfo", encoding="utf-8") as cpuinfo:
                for line in cpuinfo:
                    if line.startswith("model name"):
                        return line.split(":", 1)[1].strip()
        except OSError:
            pass
    return name


@_cached(None)
def _postgres_version() -> str:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SHOW server_version")
            return cursor.fetchone()[0].split(" ")[0]
    except Exception:  # noqa: BLE001
        return "—"


def _disks() -> list[Disk]:
    app = app_disk().lower()
    disks, seen = [], set()
    for part in psutil.disk_partitions(all=False):
        if part.mountpoint in seen or "cdrom" in part.opts or not part.fstype:
            continue
        seen.add(part.mountpoint)
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue
        disks.append(Disk(mount=part.mountpoint, fstype=part.fstype, used=usage.used,
                          total=usage.total, percent=usage.percent,
                          is_app=part.mountpoint.lower() == app))
    return sorted(disks, key=lambda d: (not d.is_app, d.mount))


@_cached(10)
def _top_processes(limit: int = 8) -> list[Proc]:
    """Busiest processes by memory, with CPU % measured since the previous call."""
    me = os.getpid()
    alive, rows = set(), []
    with _lock:
        for proc in psutil.process_iter(["pid", "name"]):
            pid = proc.info["pid"]
            alive.add(pid)
            cached = _processes.setdefault(pid, proc)
            try:
                with cached.oneshot():
                    rss = cached.memory_info().rss
                    cpu = cached.cpu_percent(interval=None)
                name = proc.info["name"] or str(pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
            if pid == 0:  # the idle "process" on Windows
                continue
            rows.append(Proc(pid=pid, name=name, cpu=cpu / (psutil.cpu_count() or 1),
                             rss=rss, is_self=pid == me))
        for pid in set(_processes) - alive:
            _processes.pop(pid, None)
    return sorted(rows, key=lambda p: p.rss, reverse=True)[:limit]


def snapshot() -> Snapshot:
    now = timezone.now()
    memory = psutil.virtual_memory()
    swap = psutil.swap_memory()
    frequency = psutil.cpu_freq()
    try:
        load = psutil.getloadavg() if os.name != "nt" else None
    except (AttributeError, OSError):
        load = None
    me = psutil.Process()
    cpu_total, per_core = cpu_now()
    db_size, db_connections = _database()
    with me.oneshot():
        process_rss = me.memory_info().rss
        process_threads = me.num_threads()
        process_started = datetime.fromtimestamp(me.create_time(),
                                                 tz=timezone.get_current_timezone())
    return Snapshot(
        taken_at=now, host=socket.gethostname(),
        os=f"{platform.system()} {platform.release()}", cpu_model=_cpu_model(),
        cores=psutil.cpu_count(logical=False) or psutil.cpu_count() or 1,
        threads=psutil.cpu_count() or 1,
        cpu_pct=cpu_total, per_core=per_core,
        freq_mhz=frequency.current if frequency else None, load=load,
        mem_total=memory.total, mem_used=memory.total - memory.available,
        mem_available=memory.available, mem_pct=memory.percent,
        swap_total=swap.total, swap_used=swap.used, swap_pct=swap.percent,
        booted_at=datetime.fromtimestamp(psutil.boot_time(), tz=timezone.get_current_timezone()),
        python=platform.python_version(), django=django.get_version(),
        postgres=_postgres_version(), db_size=db_size, db_connections=db_connections,
        process_rss=process_rss, process_cpu=me.cpu_percent(interval=None),
        process_threads=process_threads, process_started=process_started,
        disks=_disks(), processes=_top_processes(),
    )
