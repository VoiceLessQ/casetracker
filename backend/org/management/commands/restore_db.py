"""Decrypt an encrypted backup (from backup_db) to a SQLite file.

Restore is deliberate and destructive, so it never overwrites an existing file
without --force, and never touches the live database path implicitly — you pass
an explicit --output and swap it in yourself.
"""
from pathlib import Path

from cryptography.fernet import InvalidToken
from django.core.management.base import BaseCommand, CommandError

from people import crypto


class Command(BaseCommand):
    help = "Decrypt an encrypted database backup to a SQLite file."

    def add_arguments(self, parser):
        parser.add_argument("encrypted_file", help="Path to the .enc backup.")
        parser.add_argument("--output", required=True, help="Where to write the decrypted .sqlite3.")
        parser.add_argument("--force", action="store_true", help="Overwrite --output if it exists.")

    def handle(self, *args, **options):
        src = Path(options["encrypted_file"])
        out = Path(options["output"])
        if not src.exists():
            raise CommandError(f"Encrypted backup not found: {src}")
        if out.exists() and not options["force"]:
            raise CommandError(f"{out} already exists. Use --force to overwrite.")
        try:
            raw = crypto.backup_decrypt_bytes(src.read_bytes())
        except InvalidToken:
            raise CommandError(
                "Could not decrypt — wrong key. The BACKUP/FIELD encryption key "
                "must match the one used when the backup was written."
            )
        out.write_bytes(raw)
        self.stdout.write(self.style.SUCCESS(f"Restored decrypted database to: {out}"))
