# CaseTracker — municipal case-status overlay
# Copyright 2026 VoiceLessQ — https://github.com/VoiceLessQ
# Licensed under the Apache License 2.0; see LICENSE and NOTICE.
"""Journalizing — registering a document as a formal record on a case.

Records-management concepts (ISO 15489-ish, kept generic so this isn't tied to
one authority's scheme):
  - A journalized document has a unique journal NUMBER, a journal DATE, a
    DIRECTION (incoming/outgoing/internal), and is IMMUTABLE afterwards.
  - The number is a running sequence within the case (sagsnr + løbenr), the most
    portable scheme. The format is centralised here and overridable via
    settings.JOURNAL_NUMBER_FORMAT (placeholders: {ref}, {seq}), so a global
    yearly register or another convention is a one-line change, not a rewrite.
"""
from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from people.access import can_open_person_documents


class JournalError(Exception):
    """Raised when a document cannot be journalized."""


def format_journal_number(case_ref, seq):
    fmt = getattr(settings, "JOURNAL_NUMBER_FORMAT", "{ref}-{seq:03d}")
    return fmt.format(ref=case_ref, seq=seq)


def journalize(document, user):
    """Register `document` as a record on its case. Assigns the next sequence
    number for that case under a row lock (safe against concurrent journalizing),
    stamps the date and actor, and returns the journal number.

    Raises JournalError if the document has no case, is already journalized, or
    the user has no access to the (shielded) person's documents."""
    if document.case_id is None:
        raise JournalError("A document must belong to a case before it can be journalized.")
    if document.is_journalized:
        raise JournalError(f"Already journalized as {document.journal_number}.")
    if not can_open_person_documents(user, document.person):
        raise JournalError("You do not have access to this person's documents.")

    with transaction.atomic():
        # Lock the case row so two concurrent journalizings can't claim the same
        # sequence number.
        from .models import Case, Document

        Case.objects.select_for_update().get(pk=document.case_id)
        last = (
            Document.objects.filter(case_id=document.case_id, journal_sequence__isnull=False)
            .aggregate(n=Max("journal_sequence"))["n"]
            or 0
        )
        seq = last + 1
        document.journal_sequence = seq
        document.journal_number = format_journal_number(document.case.ref, seq)
        document.journalized_at = timezone.now()
        document.journalized_by = user
        document.save(
            update_fields=["journal_sequence", "journal_number", "journalized_at", "journalized_by"]
        )
    return document.journal_number
