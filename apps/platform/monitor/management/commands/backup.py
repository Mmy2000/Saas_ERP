"""manage.py backup: dump the database and zip the uploaded files now (see monitor.backups)."""

from django.core.management.base import BaseCommand, CommandError

from apps.platform.monitor.backups import BackupError, run_backup


class Command(BaseCommand):
    help = "Back up the database (every client) and the uploaded files to BACKUP_DIR."

    def handle(self, *args, **options):
        try:
            run = run_backup()
        except BackupError as exc:
            raise CommandError(f"Backup failed: {exc}") from exc
        self.stdout.write(self.style.SUCCESS(
            f"{run.database_file} ({run.database_bytes:,} bytes)\n"
            f"{run.media_file} ({run.media_bytes:,} bytes)"))
