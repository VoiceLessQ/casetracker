"""The explicit access checkpoint for OPENING a person's documents.

Guardrail #3: navigation is not access. Reaching a person via search or the
family tree never implies permission to open their documents. Opening is a
separate decision, made here and logged by the caller.

Policy:
  - Superusers may open anything.
  - A non-shielded person's documents follow normal visibility (department
    scope already limits what a worker can see) — opening is allowed but still
    logged.
  - A SHIELDED person's documents may be opened only by a user holding an
    active PersonAccessGrant. Everyone else is blocked, even if they can
    navigate to the person.
"""
from datetime import date

from django.db.models import Q


def can_open_person_documents(user, person, on=None):
    if getattr(user, "is_superuser", False):
        return True
    if person is None:
        return True            # no person to shield; department scope governs
    if not person.is_shielded:
        return True
    from .models import PersonAccessGrant

    on = on or date.today()
    return (
        PersonAccessGrant.objects.filter(user=user, person=person)
        .filter(Q(expires_on__isnull=True) | Q(expires_on__gte=on))
        .exists()
    )
