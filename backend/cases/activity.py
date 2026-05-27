# CaseTracker — municipal case-status overlay
# Copyright 2026 VoiceLessQ — https://github.com/VoiceLessQ
# Licensed under the Apache License 2.0; see LICENSE and NOTICE.
"""Append-only document-activity logging, so nothing enters or changes silently.

Called from the admin paths that mutate documents (add/edit/remove) and from the
journalize service, always with the acting user. Snapshots the label/case/person
so the trail survives even if the document is later deleted.
"""


def log_document_activity(actor, document, action, detail=""):
    from .models import DocumentActivity

    DocumentActivity.objects.create(
        actor=actor,
        action=action,
        document=document,
        document_label=document.label,
        case=document.case if document.case_id else None,
        person=document.person if document.person_id else None,
        detail=detail,
    )
