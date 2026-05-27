# CaseTracker — municipal case-status overlay
# Copyright 2026 VoiceLessQ — https://github.com/VoiceLessQ
# Licensed under the Apache License 2.0; see LICENSE and NOTICE.
"""Write an encrypted snapshot of the database.

The resulting .enc file is ciphertext: the key lives in config (a secret
manager in production), not in the backup, so a copied/leaked backup is useless
on its own. The raw SQLite file is snapshotted (so field-level encryption such
as the CPR stays intact inside it) and then the whole file is encrypted again —
double protection: the backup key opens the file, the field key is still needed
for the crown jewels.

Deployment note: this protects the BACKUP artifact. Also run the live database
on an ENCRYPTED VOLUME (LUKS / cloud disk encryption) so the working file and
WAL/journal are protected at rest too. For Postgres, pipe `pg_dump` through this
same key instead (the SQLite path below is prototype-specific).
"""
import datetime as dt
import shutil
import sqlite3
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from people import crypto


class Command(BaseCommand):
    help = "Write an AES/Fernet-encrypted snapshot of the SQLite database."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output-dir",
            default=None,
            help="Where to write the .enc file (default: <BASE_DIR>/backups).",
        )
        parser.add_argument(
            "--also-copy-to",
            action="append",
            default=[],
            metavar="DIR",
            help="Also drop the encrypted backup into this directory (repeatable). "
                 "Point it at a DIFFERENT volume / mounted offsite share — a second "
                 "folder on the same disk isn't real redundancy. The .enc is "
                 "ciphertext, so copying it to less-trusted storage is safe.",
        )

    def handle(self, *args, **options):
        db = settings.DATABASES["default"]
        if "sqlite3" not in db["ENGINE"]:
            raise CommandError(
                "backup_db supports SQLite only. For Postgres, pipe pg_dump "
                "through the same backup key (see the module docstring)."
            )
        src = Path(db["NAME"])
        if not src.exists():
            raise CommandError(f"Database file not found: {src}")

        out_dir = Path(options["output_dir"] or (settings.BASE_DIR / "backups"))
        out_dir.mkdir(parents=True, exist_ok=True)

        # Consistent snapshot via the SQLite backup API (safe even mid-write).
        tmp_path = Path(tempfile.mkstemp(suffix=".sqlite3")[1])
        try:
            with sqlite3.connect(str(src)) as srccon, sqlite3.connect(str(tmp_path)) as dstcon:
                srccon.backup(dstcon)
            raw = tmp_path.read_bytes()
        finally:
            tmp_path.unlink(missing_ok=True)

        token = crypto.backup_encrypt_bytes(raw)
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        out = out_dir / f"casetracker-{stamp}.sqlite3.enc"
        out.write_bytes(token)

        copies = []
        for extra in options["also_copy_to"]:
            dest_dir = Path(extra)
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / out.name
            shutil.copy2(out, dest)
            copies.append(str(dest))

        msg = (
            f"Encrypted backup written: {out} "
            f"({len(raw)} bytes plaintext -> {len(token)} bytes ciphertext)."
        )
        if copies:
            msg += "\nAlso copied to:\n  " + "\n  ".join(copies)
        msg += f"\nRestore with: manage.py restore_db {out} --output restored.sqlite3"
        self.stdout.write(self.style.SUCCESS(msg))
