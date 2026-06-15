# CaseTracker — municipal case-status overlay
# Copyright 2026 VoiceLessQ — https://github.com/VoiceLessQ
# Licensed under the Apache License 2.0; see LICENSE and NOTICE.
"""Append-only enforcement for audit / running-record models."""


class AppendOnly:
    """Mixin for audit and running-record models: rows may be created but never
    updated or deleted through the ORM.

    Append-only is the integrity guarantee for these records, so it is enforced
    here at the model layer rather than only in the admin — that closes the
    shell, management-command, and future-API bypass that admin-only checks
    leave open. Mix it in BEFORE ``models.Model`` so these overrides win.

    Note: a bulk ``QuerySet.delete()`` still goes around ``Model.delete()`` (a
    Django limitation); a database trigger is the deepest guarantee if one is
    ever needed.
    """

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError(
                f"{type(self).__name__} is append-only; existing rows cannot be modified."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError(
            f"{type(self).__name__} is append-only; rows cannot be deleted."
        )
