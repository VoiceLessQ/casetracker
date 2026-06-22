# CaseTracker — municipal case-status overlay
# Copyright 2026 VoiceLessQ — https://github.com/VoiceLessQ
# Licensed under the Apache License 2.0; see LICENSE and NOTICE.
"""Case escalation — raising a flag that a case needs a head's attention.

A flag is the deliberate counterpart to `Case.objects.stale()`: stale() only
*surfaces* unexplained silence, while a flag is a worker explicitly addressing a
case to a department head. It can target a department other than the holder —
the case a department keeps having handed back to it, for instance — and it
works off the case id and its routing trail, never the person's content.

Detection (`Case.objects.bouncing()`) and escalation are kept apart on purpose:
bouncing() lists ping-pong candidates, a human decides which to flag. Flagging
is never automatic — pinging stays opt-in (alert fatigue).
"""
from django.utils import timezone


class EscalationError(Exception):
    """Raised when a flag cannot be raised or resolved."""


def raise_flag(case, raised_by, kind=None, text="", to_department=None):
    """Open a flag on `case`. `to_department` defaults to the holding
    department; pass another to address heads of the department the case keeps
    coming back to. One open flag of a kind at a time keeps the list honest."""
    from .models import CaseFlag

    kind = kind or CaseFlag.Kind.ATTENTION
    if case.flags.filter(kind=kind, status=CaseFlag.Status.OPEN).exists():
        raise EscalationError(f"This case already has an open {kind} flag.")
    return CaseFlag.objects.create(
        case=case, raised_by=raised_by, kind=kind, text=text,
        to_department=to_department or case.owner_department,
    )


def acknowledge_flag(flag, user):
    """Mark an open flag as seen by the addressed department."""
    from .models import CaseFlag

    if flag.status != CaseFlag.Status.OPEN:
        return flag
    flag.status = CaseFlag.Status.ACKNOWLEDGED
    flag.save(update_fields=["status"])
    return flag


def resolve_flag(flag, user, note=""):
    """Close a flag and record who closed it and why."""
    from .models import CaseFlag

    if flag.status == CaseFlag.Status.RESOLVED:
        raise EscalationError("This flag is already resolved.")
    flag.status = CaseFlag.Status.RESOLVED
    flag.resolved_by = user
    flag.resolved_at = timezone.now()
    flag.resolution_note = note
    flag.save(update_fields=["status", "resolved_by", "resolved_at", "resolution_note"])
    return flag
