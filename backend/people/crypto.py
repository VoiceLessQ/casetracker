"""At-rest field encryption + blind index for searchable encrypted fields.

The point: a stolen database (or backup) shows only ciphertext for encrypted
columns, because the key lives in the app's config (settings.FIELD_ENCRYPTION_KEY
-> a secret manager in production), never in the data.

Encryption (Fernet/AES) is non-deterministic, so an encrypted column can't be
queried with `=` or `icontains`. To still look a CPR up, we store a separate
BLIND INDEX: a keyed HMAC of the normalised CPR. Same CPR -> same hash, so exact
(dash-tolerant) lookup works without ever decrypting, while the hash reveals
nothing about the value to someone who only has the database.

Both subkeys are derived from one master secret via HKDF, so there is a single
key to manage operationally.
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


def _derive(info, length=32, master=None):
    secret = (master or settings.FIELD_ENCRYPTION_KEY).encode()
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=None, info=info).derive(secret)


@lru_cache(maxsize=1)
def _fernet():
    return Fernet(base64.urlsafe_b64encode(_derive(b"casetracker-field-encryption")))


@lru_cache(maxsize=1)
def _bidx_key():
    return _derive(b"casetracker-cpr-blind-index")


@lru_cache(maxsize=1)
def _backup_fernet():
    # Backups need a STABLE key so any backup can be restored later. Derived from
    # a dedicated BACKUP_ENCRYPTION_KEY if set, otherwise from the master field
    # key, via a distinct HKDF context so it isn't the same key as the fields.
    master = getattr(settings, "BACKUP_ENCRYPTION_KEY", "") or settings.FIELD_ENCRYPTION_KEY
    return Fernet(base64.urlsafe_b64encode(_derive(b"casetracker-backup", master=master)))


def backup_encrypt_bytes(data):
    return _backup_fernet().encrypt(data)


def backup_decrypt_bytes(token):
    return _backup_fernet().decrypt(token)


def encrypt(text):
    if text is None or text == "":
        return text
    return _fernet().encrypt(text.encode()).decode()


def decrypt(token):
    if token is None or token == "":
        return token
    return _fernet().decrypt(token.encode()).decode()


def try_decrypt(token):
    """Decrypt a token, or return it unchanged if it is already plaintext.
    Used by the data migration so it is safe to re-run."""
    if token is None or token == "":
        return token
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return token


def normalize_cpr(value):
    """Strip everything but digits, so '010190-1234' and '0101901234' match."""
    return re.sub(r"\D", "", value or "")


def cpr_blind_index(value):
    """Keyed HMAC of the normalised CPR, or None for an empty value."""
    norm = normalize_cpr(value)
    if not norm:
        return None
    return hmac.new(_bidx_key(), norm.encode(), hashlib.sha256).hexdigest()


def blind_index_for_term(term):
    """Return a blind index if the search term looks like a CPR (>= 6 digits),
    else None. Matches the full normalised CPR only (exact, dash-tolerant)."""
    if len(normalize_cpr(term)) >= 6:
        return cpr_blind_index(term)
    return None
