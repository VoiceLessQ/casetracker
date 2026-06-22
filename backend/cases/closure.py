# CaseTracker — municipal case-status overlay
# Copyright 2026 VoiceLessQ — https://github.com/VoiceLessQ
# Licensed under the Apache License 2.0; see LICENSE and NOTICE.
"""Closing and reopening a case.

A case is never truly closed. CLOSED is a *reversible* state — concluded and
dormant, but the case, its folder, relationships and history all persist
untouched, and reopening is always available. Closing and reopening are
deliberate, reason-bearing actions (like a handoff), each recorded on the
append-only StatusEvent trail, so the close → reopen → close history is a
permanent record rather than a silently overwritten status field.

Closing enforces completeness (the same `handoff_blockers` a case must clear to
move on — required category, person, legal basis and documents): a concluded
case should carry its required legal basis. A genuinely finished-but-incomplete
case (withdrawn / rejected) can still be closed with an explicit, logged
override — the override and the outstanding items are written into the trail.
"""
from django.db import transaction
from django.utils import timezone


class ClosureError(Exception):
    """Raised when a case cannot be closed or reopened."""


def close_case(case, user, reason, override=False):
    """Conclude a case: set status CLOSED and record it. `reason` is required —
    a closure with no stated why is exactly the kind of silent state change this
    system avoids. Refuses to close a case with outstanding required items unless
    `override=True`, in which case the outstanding items are logged alongside the
    reason."""
    from .handoff import handoff_blockers
    from .models import Case, StatusEvent

    reason = (reason or "").strip()
    if not reason:
        raise ClosureError("A reason is required to close a case.")
    if case.status == Case.Status.CLOSED:
        raise ClosureError("This case is already closed.")

    blockers = handoff_blockers(case)
    if blockers and not override:
        raise ClosureError(
            "Case is not complete enough to close — " + "; ".join(blockers)
            + ". Close with an override to conclude it anyway (logged)."
        )

    note = f"Closed: {reason}"
    if blockers:
        note += " [override — outstanding: " + "; ".join(blockers) + "]"

    with transaction.atomic():
        old_status = case.status
        case.status = Case.Status.CLOSED
        case.save(update_fields=["status"])
        StatusEvent.objects.create(
            case=case, actor=user,
            from_status=old_status, to_status=case.status,
            from_department=case.owner_department, to_department=case.owner_department,
            note=note[:500],
        )
    return case


def reopen_case(case, user, reason):
    """Revive a closed case: set status IN_PROGRESS and record why. Only a CLOSED
    case is reopened here — a DONE case re-opens for the receiving department
    through a handoff, not this path."""
    from .models import Case, StatusEvent

    reason = (reason or "").strip()
    if not reason:
        raise ClosureError("A reason is required to reopen a case.")
    if case.status != Case.Status.CLOSED:
        raise ClosureError("Only a closed case can be reopened.")

    with transaction.atomic():
        old_status = case.status
        case.status = Case.Status.IN_PROGRESS
        case.save(update_fields=["status"])
        StatusEvent.objects.create(
            case=case, actor=user,
            from_status=old_status, to_status=case.status,
            from_department=case.owner_department, to_department=case.owner_department,
            note=f"Reopened: {reason}"[:500],
        )
    return case
