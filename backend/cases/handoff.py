"""Case handoff — a gated move of a case to another department.

Moving a case is not free: the receiving department must not inherit an
incomplete case. So a handoff is requested, then approved by a department head
(a Lead of the holding department), and approval is blocked until the case is
complete. On approval the case's owner_department moves and a StatusEvent
records the handoff.
"""
from django.db import transaction
from django.utils import timezone

from org.models import Membership


class HandoffError(Exception):
    """Raised when a handoff cannot be requested or approved."""


def is_department_head(user, department_id):
    """A department head = a Lead of that department (or a superuser)."""
    if getattr(user, "is_superuser", False):
        return True
    return user.memberships.filter(
        department_id=department_id, role=Membership.Role.LEAD
    ).exists()


def handoff_blockers(case):
    """Human-readable list of what's missing before this case may move on.
    Empty list = ready. Reuses the regulation engine: every in-effect rule at
    level=required must have its reference actually linked to the case."""
    from .models import RegulationRule

    blockers = []
    if not case.category_id:
        blockers.append("Category not set")
    if not case.person_id:
        blockers.append("No person linked")
    linked = set(case.legal_refs.values_list("id", flat=True))
    for rule in case.applicable_rules():
        if rule.level == RegulationRule.Level.REQUIRED and rule.reference_id not in linked:
            blockers.append(f"Missing required legal basis: {rule.reference}")

    # Required document types for the category must each be attached to the case.
    if case.category_id:
        present = set(
            case.documents.filter(document_type__isnull=False)
            .values_list("document_type_id", flat=True)
        )
        for dt in case.category.required_document_types.all():
            if dt.id not in present:
                blockers.append(f"Missing required document: {dt.name}")
    return blockers


def request_handoff(case, to_department, requested_by, note=""):
    from .models import CaseHandoff

    if to_department.id == case.owner_department_id:
        raise HandoffError("The case is already owned by that department.")
    if case.handoffs.filter(status=CaseHandoff.Status.PENDING).exists():
        raise HandoffError("There is already a pending handoff for this case.")
    return CaseHandoff.objects.create(
        case=case, from_department=case.owner_department, to_department=to_department,
        requested_by=requested_by, note=note,
    )


def approve_handoff(handoff, approver, note=""):
    """Approve and perform the move. Requires the approver to be a head of the
    holding department and the case to be complete. Moves owner_department and
    records a StatusEvent, atomically."""
    from .models import CaseHandoff, StatusEvent

    if handoff.status != CaseHandoff.Status.PENDING:
        raise HandoffError("This handoff has already been decided.")
    case = handoff.case
    if not is_department_head(approver, case.owner_department_id):
        raise HandoffError(
            "Only a department head (Lead) of the holding department can approve a handoff."
        )
    blockers = handoff_blockers(case)
    if blockers:
        raise HandoffError("Case is not ready to move on — " + "; ".join(blockers))

    with transaction.atomic():
        old_department = case.owner_department
        case.owner_department = handoff.to_department
        case.save(update_fields=["owner_department"])
        StatusEvent.objects.create(
            case=case, actor=approver,
            from_status=case.status, to_status=case.status,
            from_department=old_department, to_department=handoff.to_department,
            note=f"Handoff approved: {handoff.note}"[:500],
        )
        handoff.status = CaseHandoff.Status.APPROVED
        handoff.decided_by = approver
        handoff.decided_at = timezone.now()
        handoff.decision_note = note
        handoff.save(update_fields=["status", "decided_by", "decided_at", "decision_note"])
    return case


def reject_handoff(handoff, decider, note=""):
    from .models import CaseHandoff

    if handoff.status != CaseHandoff.Status.PENDING:
        raise HandoffError("This handoff has already been decided.")
    if not is_department_head(decider, handoff.case.owner_department_id):
        raise HandoffError("Only a department head (Lead) of the holding department can reject.")
    handoff.status = CaseHandoff.Status.REJECTED
    handoff.decided_by = decider
    handoff.decided_at = timezone.now()
    handoff.decision_note = note
    handoff.save(update_fields=["status", "decided_by", "decided_at", "decision_note"])
    return handoff
