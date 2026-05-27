# CaseTracker — municipal case-status overlay
# Copyright 2026 VoiceLessQ — https://github.com/VoiceLessQ
# Licensed under the Apache License 2.0; see LICENSE and NOTICE.
"""At-rest field encryption + blind index for searchable encrypted fields.

The point: a stolen database (or backup) shows only ciphertext for encrypted
columns, because the key lives in the app's config (settings.FIELD_ENCRYPTION_KEY
-> a secret manager / mounted file in production), never in the data.

Encryption (Fernet/AES) is non-deterministic, so an encrypted column can't be
queried with `=` or `icontains`. To still look a CPR up, we store a separate
BLIND INDEX: a keyed HMAC of the normalised CPR. Same CPR -> same hash, so exact
(dash-tolerant) lookup works without ever decrypting, while the hash reveals
nothing about the value to someone who only has the database.

PLUGGABLE: the built-in Fernet+HKDF provider is the default/base. A deployer who
wants their own encryption (their KMS/HSM, a different cipher, envelope
encryption) sets `FIELD_ENCRYPTION_BACKEND` to a dotted path of a CryptoProvider
subclass — the rest of the app calls the module-level functions unchanged. The
master secret itself can also come from a mounted file/secret (see settings:
FIELD_ENCRYPTION_KEY_FILE), so the key can live on the deployer's own network.
"""
import base64
import hashlib
import hmac
import re
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings
from django.utils.module_loading import import_string


def _derive(info, length=32, master=None):
    secret = (master or settings.FIELD_ENCRYPTION_KEY).encode()
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=None, info=info).derive(secret)


class CryptoProvider:
    """Interface for at-rest field crypto. Implement these to bring your own
    encryption (HSM/KMS/different cipher) and point FIELD_ENCRYPTION_BACKEND at
    your subclass. Note: switching providers does not re-encrypt existing data —
    a key/provider change needs a decrypt-old / encrypt-new migration. And the
    blind index must use a key your provider can reproduce, or CPR search breaks."""

    def encrypt(self, text):                    # str -> str (ciphertext)
        raise NotImplementedError

    def decrypt(self, token):                   # str -> str (plaintext)
        raise NotImplementedError

    def try_decrypt(self, token):
        """Decrypt, or return the input unchanged if it isn't ours (used by data
        migrations for idempotency). Override for a tighter exception type."""
        try:
            return self.decrypt(token)
        except Exception:
            return token

    def blind_index(self, normalized):          # normalised str -> hex digest
        raise NotImplementedError

    def backup_encrypt(self, data):             # bytes -> bytes
        raise NotImplementedError

    def backup_decrypt(self, token):            # bytes -> bytes
        raise NotImplementedError


class FernetProvider(CryptoProvider):
    """Default/base provider: Fernet (AES-128-CBC + HMAC) for fields and backups,
    keyed-HMAC-SHA256 for the blind index, all derived from one master secret via
    HKDF. Behaviour is unchanged from the original implementation."""

    def __init__(self):
        self._fernet = Fernet(base64.urlsafe_b64encode(_derive(b"casetracker-field-encryption")))
        self._bidx_key = _derive(b"casetracker-cpr-blind-index")
        backup_master = getattr(settings, "BACKUP_ENCRYPTION_KEY", "") or settings.FIELD_ENCRYPTION_KEY
        self._backup_fernet = Fernet(
            base64.urlsafe_b64encode(_derive(b"casetracker-backup", master=backup_master))
        )

    def encrypt(self, text):
        return self._fernet.encrypt(text.encode()).decode()

    def decrypt(self, token):
        return self._fernet.decrypt(token.encode()).decode()

    def try_decrypt(self, token):
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken:
            return token

    def blind_index(self, normalized):
        return hmac.new(self._bidx_key, normalized.encode(), hashlib.sha256).hexdigest()

    def backup_encrypt(self, data):
        return self._backup_fernet.encrypt(data)

    def backup_decrypt(self, token):
        return self._backup_fernet.decrypt(token)


@lru_cache(maxsize=1)
def _provider():
    path = getattr(settings, "FIELD_ENCRYPTION_BACKEND", "") or ""
    return import_string(path)() if path else FernetProvider()


# --- public API (unchanged signatures; delegate to the active provider) --------

def encrypt(text):
    if text is None or text == "":
        return text
    return _provider().encrypt(text)


def decrypt(token):
    if token is None or token == "":
        return token
    return _provider().decrypt(token)


def try_decrypt(token):
    """Decrypt a token, or return it unchanged if it is already plaintext.
    Used by the data migrations so they are safe to re-run."""
    if token is None or token == "":
        return token
    return _provider().try_decrypt(token)


def backup_encrypt_bytes(data):
    return _provider().backup_encrypt(data)


def backup_decrypt_bytes(token):
    return _provider().backup_decrypt(token)


def normalize_cpr(value):
    """Strip everything but digits, so '010190-1234' and '0101901234' match."""
    return re.sub(r"\D", "", value or "")


def cpr_blind_index(value):
    """Keyed HMAC of the normalised CPR, or None for an empty value."""
    norm = normalize_cpr(value)
    if not norm:
        return None
    return _provider().blind_index(norm)


def blind_index_for_term(term):
    """Return a blind index if the search term looks like a CPR (>= 6 digits),
    else None. Matches the full normalised CPR only (exact, dash-tolerant)."""
    if len(normalize_cpr(term)) >= 6:
        return cpr_blind_index(term)
    return None
