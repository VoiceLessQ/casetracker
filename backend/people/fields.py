from django.db import models

from . import crypto


class EncryptedFieldMixin:
    """Encrypts at rest: the DB column is ordinary text but always holds
    ciphertext, so a raw dump or stolen backup reveals nothing without the key.
    Decrypts transparently on load, encrypts on save.

    Because encryption is non-deterministic, you cannot filter on this field
    with `=`/`icontains` — use a blind-index column if you need to look it up."""

    def from_db_value(self, value, expression, connection):
        return crypto.decrypt(value)

    def get_prep_value(self, value):
        return crypto.encrypt(super().get_prep_value(value))


class EncryptedCharField(EncryptedFieldMixin, models.CharField):
    pass


class EncryptedTextField(EncryptedFieldMixin, models.TextField):
    pass
