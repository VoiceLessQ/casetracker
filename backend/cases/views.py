from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render

from .models import Case, CaseAssignment, FollowUp


@staff_member_required
def dashboard(request):
    """A worker's worklist — a read-only view over the already-scoped data, so
    it physically can only show what this user is allowed to see. Actions drop
    back into the admin for now. Honours impersonation (the middleware has
    already swapped request.user), so 'view as' shows that worker's dashboard."""
    user = request.user
    dept_ids = list(user.memberships.values_list("department_id", flat=True))

    if user.is_superuser:
        in_scope = Case.objects.all()
    else:
        in_scope = Case.objects.filter(owner_department_id__in=dept_ids)
    open_cases = in_scope.exclude(status=Case.Status.DONE)

    my_case_ids = CaseAssignment.objects.filter(
        worker=user, active=True
    ).values_list("case_id", flat=True)
    my_cases = open_cases.filter(id__in=my_case_ids).select_related("person", "owner_department")

    my_tasks = (
        FollowUp.objects.filter(assignee=user, done=False)
        .select_related("case", "case__person")
        .order_by("due_date")
    )

    needs_attention = Case.objects.stale()
    if not user.is_superuser:
        needs_attention = needs_attention.filter(owner_department_id__in=dept_ids)
    needs_attention = needs_attention.select_related("person", "owner_department")

    assigned_ids = CaseAssignment.objects.filter(active=True).values_list("case_id", flat=True)
    queue = open_cases.exclude(id__in=assigned_ids).select_related("person", "owner_department")

    context = {
        "worker": user,
        "departments": list(user.memberships.select_related("department")),
        "impersonator": getattr(request, "impersonator", None),
        "my_cases": my_cases,
        "my_tasks": my_tasks,
        "needs_attention": needs_attention,
        "queue": queue,
    }
    return render(request, "dashboard.html", context)
