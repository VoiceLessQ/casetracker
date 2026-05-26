from django.db import models

from . import crypto


class EncryptedCharField(models.CharField):
    """A CharField whose value is encrypted at rest. The DB column is ordinary
    text, but it always holds ciphertext — a raw dump or stolen backup reveals
    nothing without the key. Decrypts transparently on load, encrypts on save.

    Note: because encryption is non-deterministic, you cannot filter on this
    field with `=`/`icontains`. Use a blind-index column for lookups."""

    def from_db_value(self, value, expression, connection):
        return crypto.decrypt(value)

    def get_prep_value(self, value):
        return crypto.encrypt(super().get_prep_value(value))
