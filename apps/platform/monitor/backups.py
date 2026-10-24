"""Backups: the whole database (every client) and the uploaded files, as one dated pair of files
under BACKUP_DIR, the newest BACKUP_KEEP of each kept.

    gweb-db-20261013-023000.dump     pg_dump custom format (restore with pg_restore)
    gweb-media-20261013-023000.zip   MEDIA_ROOT: logos, attachments, signatures…

The dump runs as BACKUP_DATABASE_URL's role, which must bypass row-level security
(app_platform in ops/db/bootstrap.sql): every client table forces RLS, so any other role would
dump nothing or fail. Copy the files off the server too (another disk, cloud storage): a backup
on the same disk is lost with it. The encryption keys (FIELD_ENCRYPTION_KEY, BLIND_INDEX_KEY)
are not in the dump and must be kept safe separately.

Every new pair is read back before the run counts as good: the dump's table of contents
(pg_restore --list) and the archive's checksums. `manage.py verify_backup --into <scratch
database>` goes further and restores one (docs/operations/README.md, TD-04).
"""

from __future__ import annotations

import os
import subprocess
import zipfile
from pathlib import Path

import environ
from django.conf import settings
from django.utils import timezone

from .models import BackupRun


class BackupError(Exception):
    pass


def _dump_database(target: Path) -> None:
    url = settings.BACKUP_DATABASE_URL
    if not url:
        raise BackupError("BACKUP_DATABASE_URL is not set: give it a role that bypasses "
                          "row-level security (app_platform).")
    arguments, environment = _connection_args(url)
    command = [settings.BACKUP_PG_DUMP, "--format=custom", "--no-owner", "--file", str(target),
               *arguments]
    try:
        done = subprocess.run(command, env=environment, capture_output=True, text=True,
                              timeout=60 * 60, check=False)
    except FileNotFoundError:
        raise BackupError(f"{settings.BACKUP_PG_DUMP} was not found: install the PostgreSQL "
                          "client tools or set BACKUP_PG_DUMP to pg_dump's full path.") from None
    if done.returncode != 0:
        raise BackupError((done.stderr or done.stdout or "pg_dump failed").strip()[-4000:])


def _connection_args(url: str) -> tuple[list[str], dict]:
    db = environ.Env.db_url_config(url)
    args = ["--dbname", db["NAME"]]
    if db.get("HOST"):
        args += ["--host", str(db["HOST"])]
    if db.get("PORT"):
        args += ["--port", str(db["PORT"])]
    if db.get("USER"):
        args += ["--username", str(db["USER"])]
    return args, {**os.environ, "PGPASSWORD": str(db.get("PASSWORD") or "")}


def _pg_restore(arguments: list[str], *, env=None, timeout: int = 600):
    try:
        return subprocess.run([settings.BACKUP_PG_RESTORE, *arguments], env=env,
                              capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError:
        raise BackupError(f"{settings.BACKUP_PG_RESTORE} was not found: install the PostgreSQL "
                          "client tools or set BACKUP_PG_RESTORE to pg_restore's full path."
                          ) from None


def check_dump(path: Path) -> int:
    """Read a dump's table of contents back: a truncated or damaged file fails here, not on the
    day it is needed. Returns how many tables' rows it holds."""
    done = _pg_restore(["--list", str(path)])
    if done.returncode != 0:
        raise BackupError("The dump can't be read back: "
                          + (done.stderr or done.stdout or "pg_restore failed").strip()[-2000:])
    tables = sum(1 for line in done.stdout.splitlines() if " TABLE DATA " in line)
    if not tables:
        raise BackupError("The dump holds no table rows.")
    return tables


def check_media(path: Path) -> int:
    """Every file in the media archive reads back with its checksum. Returns how many."""
    try:
        with zipfile.ZipFile(path) as archive:
            damaged = archive.testzip()
            names = archive.namelist()
    except (OSError, zipfile.BadZipFile) as exc:
        raise BackupError(f"The media archive can't be read: {exc}") from exc
    if damaged:
        raise BackupError(f"The media archive is damaged at {damaged}.")
    return len(names)


def restore_into(dump: Path, url: str) -> None:
    """Restore `dump` into the (empty) database at `url`: a rehearsal of the real thing. The
    role must bypass row-level security to load the rows (a superuser on the scratch server)."""
    arguments, env = _connection_args(url)
    done = _pg_restore(["--no-owner", "--exit-on-error", *arguments, str(dump)], env=env,
                       timeout=4 * 60 * 60)
    if done.returncode != 0:
        raise BackupError("The restore failed: "
                          + (done.stderr or done.stdout or "pg_restore failed").strip()[-4000:])


def _zip_media(target: Path) -> None:
    root = Path(settings.MEDIA_ROOT)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        if root.exists():
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(root).as_posix())


def prune(directory: Path, keep: int) -> list[str]:
    """Delete all but the newest `keep` database dumps and media archives."""
    removed = []
    for pattern in ("gweb-db-*.dump", "gweb-media-*.zip"):
        for old in sorted(directory.glob(pattern), reverse=True)[keep:]:
            old.unlink(missing_ok=True)
            removed.append(old.name)
    return removed


def run_backup() -> BackupRun:
    """Back up now. The run is recorded either way; a failure raises BackupError after it."""
    started = timezone.now()
    run = BackupRun.objects.create(started_at=started)
    directory = Path(settings.BACKUP_DIR)
    stamp = timezone.localtime(started).strftime("%Y%m%d-%H%M%S")
    database = directory / f"gweb-db-{stamp}.dump"
    media = directory / f"gweb-media-{stamp}.zip"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        _dump_database(database)
        _zip_media(media)
        check_dump(database)
        check_media(media)
    except Exception as exc:  # noqa: BLE001 - recorded, then raised
        for partial in (database, media):
            partial.unlink(missing_ok=True)
        run.error = str(exc)[:4000] or exc.__class__.__name__
        run.finished_at = timezone.now()
        run.save()
        raise BackupError(run.error) from exc
    run.ok = True
    run.database_file, run.database_bytes = str(database), database.stat().st_size
    run.media_file, run.media_bytes = str(media), media.stat().st_size
    run.finished_at = timezone.now()
    run.save()
    prune(directory, max(settings.BACKUP_KEEP, 1))
    return run


def last_good() -> BackupRun | None:
    return BackupRun.objects.filter(ok=True).order_by("-started_at").first()
