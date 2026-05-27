# CaseTracker — municipal case-status overlay
# Copyright 2026 VoiceLessQ — https://github.com/VoiceLessQ
# Licensed under the Apache License 2.0; see LICENSE and NOTICE.
"""Encrypted document export — the deliberate, controlled leak.

The system is an index, not a vault: it stores links, not files. An export
therefore reads the linked files from the municipal drive and packs them into
a single AES-256 encrypted zip. The password is generated per export, shown
once, and never stored. Every export is logged via cases.models.ExportEvent.
"""
import hashlib
import io
import secrets
from pathlib import Path

import pyzipper
from django.conf import settings


def generate_password():
    """A strong, one-time password for a single zip. ~144 bits of entropy.
    Shown once to the exporter; never persisted."""
    return secrets.token_urlsafe(18)


def safe_drive_path(location):
    """Resolve a Document.location to a real file CONFINED to the drive root.

    `location` is admin-entered free text, so this is a security boundary: it
    refuses absolute paths that escape the drive and any '..' traversal, and
    only returns a path that genuinely sits inside MUNICIPAL_DRIVE_ROOT. Returns
    None for URLs, missing files, or anything outside the drive."""
    root = Path(settings.MUNICIPAL_DRIVE_ROOT).resolve()
    raw = (location or "").strip()
    if not raw:
        return None
    p = Path(raw)
    candidate = (p if p.is_absolute() else root / p).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None  # escapes the drive root — refuse
    return candidate if candidate.is_file() else None


def _unique_name(used, name):
    if name not in used:
        used.add(name)
        return name
    stem, dot, ext = name.partition(".")
    i = 1
    while True:
        candidate = f"{stem}_{i}{dot}{ext}"
        if candidate not in used:
            used.add(candidate)
            return candidate
        i += 1


def build_encrypted_zip(documents, password):
    """Build an AES-256 encrypted zip of the given documents.

    Files that resolve to a real file under the drive root are included;
    everything (including link-only / missing / out-of-bounds files) is recorded
    in MANIFEST.txt. Returns (zip_bytes, manifest_text, sha256_hex)."""
    manifest = ["CaseTracker encrypted export", f"documents: {len(documents)}", ""]
    used = set()
    buf = io.BytesIO()
    with pyzipper.AESZipFile(
        buf, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES
    ) as zf:
        zf.setpassword(password.encode())
        for doc in documents:
            path = safe_drive_path(doc.location)
            if path is not None:
                arcname = _unique_name(used, f"{doc.pk}_{path.name}")
                zf.write(path, arcname)
                status = f"included as {arcname}"
            else:
                status = "LINK ONLY (file not on the drive / not accessible)"
            manifest.append(
                f"- [{status}] {doc.label} | kind={doc.kind} | "
                f"case={doc.case.ref if doc.case_id else '-'} | "
                f"person={doc.person.name if doc.person_id else '-'} | "
                f"location={doc.location}"
            )
        manifest_text = "\n".join(manifest) + "\n"
        zf.writestr("MANIFEST.txt", manifest_text)
    zip_bytes = buf.getvalue()
    return zip_bytes, manifest_text, hashlib.sha256(zip_bytes).hexdigest()
