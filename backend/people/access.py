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


def _granted_person_ids(user, on=None):
    from .models import PersonAccessGrant

    on = on or date.today()
    return PersonAccessGrant.objects.filter(user=user).filter(
        Q(expires_on__isnull=True) | Q(expires_on__gte=on)
    ).values_list("person_id", flat=True)


def can_search_all(user):
    """Intake / borgerservice: a legitimately broad searchable scope (find or
    register anyone), as a ROLE — not the break-the-glass exception."""
    return user.is_superuser or user.has_perm("people.search_all_persons")


def visible_persons(user):
    """The widest set a user may ever see: everyone EXCEPT shielded persons they
    have no active grant for. Existence-hidden — a shielded record they aren't
    cleared for shouldn't surface at all. (Break-the-glass widens to this set,
    but never past shielding.)"""
    from .models import Person

    qs = Person.objects.all()
    if user.is_superuser:
        return qs
    granted = list(_granted_person_ids(user))
    return qs.exclude(Q(is_shielded=True) & ~Q(id__in=granted))


def searchable_persons(user):
    """The default search/browse scope: people you have a reason to reach —
    the subjects of your departments' cases — minus shielded-without-grant.
    Intake/superusers get the whole (non-shielded-or-granted) base."""
    base = visible_persons(user)
    if can_search_all(user):
        return base
    dept_ids = user.memberships.values_list("department_id", flat=True)
    return base.filter(cases__owner_department_id__in=dept_ids).distinct()


def log_search(user, term, results, kind, break_glass=False, reason=""):
    """Record a search. On a hit, store the matched people (the uid we now know
    they looked at); on a miss, store the raw term — so the log never becomes a
    CPR repository on successful lookups."""
    from .models import SearchEvent

    found = list(results[:50])
    event = SearchEvent.objects.create(
        actor=user, kind=kind, result_count=len(found),
        term="" if found else (term or ""),
        break_glass=break_glass, reason=reason,
    )
    if found:
        event.matched.set(found)
    return event


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
