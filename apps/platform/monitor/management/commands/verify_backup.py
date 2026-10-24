"""manage.py verify_backup: prove a backup can be restored (TD-04, docs/operations/README.md).

    python manage.py verify_backup                    # the newest good backup reads back
    python manage.py verify_backup --into postgres://postgres:…@scratch:5432/restore_test

--into restores the dump into that empty database (as a role that bypasses row-level security,
e.g. the scratch server's superuser) and counts what came back. Never the live database.
"""

from pathlib import Path

import environ
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.platform.monitor.backups import (
    BackupError,
    check_dump,
    check_media,
    last_good,
    restore_into,
)

COUNTED = ("tenants_tenant", "iam_user", "ledger_journalentry", "ledger_journalline",
           "inventory_item")


def _same_database(url: str) -> bool:
    target = environ.Env.db_url_config(url)
    for live in settings.DATABASES.values():
        if (live.get("NAME") == target.get("NAME")
                and (live.get("HOST") or "localhost") == (target.get("HOST") or "localhost")
                and str(live.get("PORT") or 5432) == str(target.get("PORT") or 5432)):
            return True
    return False


def count_rows(url: str) -> dict[str, int]:
    import psycopg

    db = environ.Env.db_url_config(url)
    with psycopg.connect(dbname=db["NAME"], host=db.get("HOST") or None,
                         port=db.get("PORT") or None, user=db.get("USER") or None,
                         password=db.get("PASSWORD") or None) as conn:
        return {table: conn.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]  # noqa: S608 - fixed names
                for table in COUNTED}


class Command(BaseCommand):
    help = "Check that the newest good backup (or --file) reads back; --into restores it."

    def add_arguments(self, parser):
        parser.add_argument("--file", help="A database dump to check instead of the newest one.")
        parser.add_argument("--into", metavar="DATABASE_URL",
                            help="Restore into this empty scratch database and count the rows.")

    def handle(self, *args, file=None, into=None, **options):
        if into and _same_database(into):
            raise CommandError("--into points at a database this site uses. Restore into an "
                               "empty scratch database, never the live one.")
        media = None
        if file:
            dump = Path(file)
        else:
            run = last_good()
            if run is None:
                raise CommandError("No good backup is recorded (Console → Backups).")
            dump, media = Path(run.database_file), Path(run.media_file)
            self.stdout.write(f"Backup of {run.started_at:%Y-%m-%d %H:%M}")
        if not dump.is_file():
            raise CommandError(f"{dump} is missing: was it moved off the server?")
        try:
            self.stdout.write(f"{dump.name}: {check_dump(dump)} tables with rows")
            if media is not None:
                self.stdout.write(f"{media.name}: {check_media(media)} files")
            if into:
                restore_into(dump, into)
                for table, rows in count_rows(into).items():
                    self.stdout.write(f"  restored {table}: {rows:,} rows")
        except BackupError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(
            "Restored and counted." if into else "The backup reads back."))
