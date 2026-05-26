import base64

from django.contrib import admin, messages
from django.db.models import Q
from django.http import FileResponse
from django.template.response import TemplateResponse
from django.utils import timezone

from org.models import Department, Membership
from people.access import can_open_person_documents
from people.admin import CprSearchMixin

from .exports import build_encrypted_zip, generate_password, safe_drive_path
from .models import (
    Case, StatusEvent, Document, FollowUp,
    CaseLog, CaseAssignment, CalendarEvent,
    LegalReference, CaseLegalRef,
    CaseCategory, RegulationRule, Circumstance, ExportEvent, DocumentAccessEvent,
)


def user_department_ids(user):
    """The department ids this user belongs to."""
    return user.memberships.values_list("department_id", flat=True)


# Per-department default access. A user's role in a department decides what
# they may do with that department's rows (guardrail #3: access is enforced,
# not assumed). Roles are ranked so checks read as "at least MEMBER".
ROLE_RANK = {
    Membership.Role.VIEWER: 1,
    Membership.Role.MEMBER: 2,
    Membership.Role.LEAD: 3,
}
MEMBER = ROLE_RANK[Membership.Role.MEMBER]
LEAD = ROLE_RANK[Membership.Role.LEAD]


def role_rank_in(user, dept_id):
    """The user's role rank in one department (0 if not a member)."""
    if dept_id is None:
        return 0
    m = user.memberships.filter(department_id=dept_id).first()
    return ROLE_RANK.get(m.role, 0) if m else 0


def max_role_rank(user):
    """The user's strongest role across all their departments."""
    return max(
        (ROLE_RANK.get(r, 0) for r in user.memberships.values_list("role", flat=True)),
        default=0,
    )


class ScopedAdmin(admin.ModelAdmin):
    """Base admin that limits visible rows to the user's departments AND gates
    edits by the user's role in the owning department. Superusers see and do
    everything. Each subclass sets `department_path` to the ORM lookup that
    reaches the owning department from that model; `edit_role` is the minimum
    role rank required to add/change rows (default: MEMBER)."""

    department_path = "owner_department_id"
    edit_role = MEMBER

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(
            **{f"{self.department_path}__in": user_department_ids(request.user)}
        )

    def _obj_department_id(self, obj):
        """Resolve the owning department id by walking `department_path`.
        Returns None if the chain breaks (e.g. a person-level Document with no
        case) — such a row has no owning department, so role checks deny it to
        non-superusers."""
        val = obj
        for part in self.department_path.split("__"):
            if val is None:
                return None
            val = getattr(val, part)
        return val

    def has_add_permission(self, request):
        if request.user.is_superuser:
            return True
        return super().has_add_permission(request) and max_role_rank(request.user) >= self.edit_role

    def has_change_permission(self, request, obj=None):
        if request.user.is_superuser:
            return True
        if not super().has_change_permission(request, obj):
            return False
        if obj is None:
            return max_role_rank(request.user) >= self.edit_role
        return role_rank_in(request.user, self._obj_department_id(obj)) >= self.edit_role

    def has_delete_permission(self, request, obj=None):
        # Deleting case-area rows is superuser-only; caseworkers close/mark
        # done rather than delete, and the append-only logs forbid it entirely.
        return request.user.is_superuser


def _log_access(user, doc, action, allowed, reason=""):
    DocumentAccessEvent.objects.create(
        opened_by=user, document=doc, document_label=doc.label, person=doc.person,
        action=action, allowed=allowed, reason=reason,
    )


@admin.action(description="Open / download selected (access-checked, logged)")
def open_document(modeladmin, request, queryset):
    """Open a single document. This is the access checkpoint (guardrail #3):
    being able to see/navigate to the document does not mean you may open it —
    a shielded person's documents need an explicit grant. Every open, allowed
    or denied, is logged."""
    docs = list(queryset)
    if len(docs) != 1:
        modeladmin.message_user(
            request, "Select exactly one document to open (use export for several).",
            messages.WARNING,
        )
        return
    doc = docs[0]
    allowed = can_open_person_documents(request.user, doc.person)
    _log_access(
        request.user, doc, DocumentAccessEvent.Action.OPEN, allowed,
        "" if allowed else "shielded person: no active access grant",
    )
    if not allowed:
        modeladmin.message_user(
            request,
            f"Access denied: {doc.person} is shielded and you have no access grant. "
            f"The attempt was logged.",
            messages.ERROR,
        )
        return
    path = safe_drive_path(doc.location)
    if path is None:
        modeladmin.message_user(
            request,
            f"'{doc.label}' is a link only — no file on the drive to open "
            f"({doc.location}). The open was logged.",
            messages.INFO,
        )
        return
    return FileResponse(open(path, "rb"), as_attachment=True, filename=path.name)


@admin.action(description="Export selected as encrypted zip")
def export_encrypted_zip(modeladmin, request, queryset):
    """High-sensitivity, deliberate leak: pack the selected documents into one
    AES-256 encrypted zip with a one-time auto-generated password (shown once,
    never stored), and log the export. Only leads/superusers may export; only
    documents within their scope (the queryset is dept-scoped); and shielded
    persons' documents are excluded unless the user has an access grant."""
    if not (request.user.is_superuser or max_role_rank(request.user) >= LEAD):
        modeladmin.message_user(
            request, "Only leads or superusers can export documents.", messages.ERROR
        )
        return
    docs = list(queryset)
    if not docs:
        modeladmin.message_user(request, "No documents selected.", messages.WARNING)
        return

    # Access gate: split into what this user may open vs. shielded-without-grant.
    allowed_docs, denied_docs = [], []
    for d in docs:
        (allowed_docs if can_open_person_documents(request.user, d.person) else denied_docs).append(d)

    common = {
        **modeladmin.admin_site.each_context(request),
        "opts": modeladmin.model._meta,
        "media": modeladmin.media,
    }

    if request.POST.get("confirm"):
        reason = (request.POST.get("reason") or "").strip()
        for d in denied_docs:
            _log_access(request.user, d, DocumentAccessEvent.Action.EXPORT, False,
                        "shielded person: no active access grant")
        if not reason:
            modeladmin.message_user(request, "A reason is required to export.", messages.ERROR)
        elif not allowed_docs:
            modeladmin.message_user(
                request,
                "All selected documents belong to shielded persons you have no "
                "access to. Nothing exported; the attempts were logged.",
                messages.ERROR,
            )
        else:
            password = generate_password()
            zip_bytes, manifest, sha256 = build_encrypted_zip(allowed_docs, password)
            ExportEvent.objects.create(
                exported_by=request.user, document_count=len(allowed_docs),
                reason=reason, manifest=manifest, sha256=sha256,
            )
            return TemplateResponse(request, "admin/cases/export_result.html", {
                **common,
                "title": "Encrypted export ready",
                "password": password,
                "sha256": sha256,
                "count": len(allowed_docs),
                "denied_count": len(denied_docs),
                "zip_b64": base64.b64encode(zip_bytes).decode("ascii"),
                "filename": f"casetracker-export-{timezone.now():%Y%m%d-%H%M%S}.zip",
            })

    return TemplateResponse(request, "admin/cases/export_confirm.html", {
        **common,
        "title": "Confirm encrypted export",
        "documents": allowed_docs,
        "denied_count": len(denied_docs),
        "selected": [str(d.pk) for d in docs],
        "action_checkbox_name": admin.helpers.ACTION_CHECKBOX_NAME,
    })


class CaseAssignmentInline(admin.TabularInline):
    """Who is working this case — shown on the case page. Reassigning/handing
    off is a LEAD action: members see assignments but can't change them."""
    model = CaseAssignment
    extra = 1
    autocomplete_fields = ("worker",)

    def _lead(self, request, case):
        if request.user.is_superuser:
            return True
        if case is None:
            return max_role_rank(request.user) >= LEAD
        return role_rank_in(request.user, case.owner_department_id) >= LEAD

    def has_add_permission(self, request, obj=None):
        return super().has_add_permission(request, obj) and self._lead(request, obj)

    def has_change_permission(self, request, obj=None):
        return super().has_change_permission(request, obj) and self._lead(request, obj)

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


class FollowUpInline(admin.TabularInline):
    """Open/closed tasks — the 'what's done / what's left' view on the case page."""
    model = FollowUp
    extra = 1


class CaseLegalRefInline(admin.TabularInline):
    """Legal bases attached to the case (e.g. the § that applies). `required`
    marks the ones the case must keep on record; `auto` shows it came from a
    rule rather than being added by hand."""
    model = CaseLegalRef
    extra = 1
    autocomplete_fields = ("reference",)
    readonly_fields = ("auto", "rule")


@admin.register(Case)
class CaseAdmin(CprSearchMixin, ScopedAdmin):
    department_path = "owner_department_id"
    cpr_bidx_paths = ("person__cpr_bidx",)
    list_display = ("ref", "title", "person", "owner_department", "status", "waiting_on", "mute_pings", "review_after", "updated_at")
    list_filter = ("owner_department", "status", "category", "mute_pings")
    search_fields = ("ref", "title", "person__name")  # CPR matched via blind index
    autocomplete_fields = ("person", "category")
    filter_horizontal = ("circumstances",)   # user-friendly dual-list selector
    inlines = [CaseAssignmentInline, FollowUpInline, CaseLegalRefInline]

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        # A case can only be owned by a department where the user is MEMBER+;
        # they can't file a case into a department they only view (or aren't in).
        if db_field.name == "owner_department" and not request.user.is_superuser:
            allowed = [
                d for d in user_department_ids(request.user)
                if role_rank_in(request.user, d) >= MEMBER
            ]
            kwargs["queryset"] = Department.objects.filter(id__in=allowed)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(StatusEvent)
class StatusEventAdmin(ScopedAdmin):
    department_path = "case__owner_department_id"
    list_display = ("case", "timestamp", "actor", "from_status", "to_status")
    list_filter = ("to_status",)


@admin.register(Document)
class DocumentAdmin(CprSearchMixin, ScopedAdmin):
    department_path = "case__owner_department_id"
    cpr_bidx_paths = ("person__cpr_bidx",)
    list_display = ("label", "kind", "case", "person", "email_from", "source", "added_by", "added_at")
    list_filter = ("kind", "source")
    search_fields = ("label", "case__ref", "location", "email_from", "email_subject")  # CPR via blind index
    actions = [open_document, export_encrypted_zip]

    def get_actions(self, request):
        actions = super().get_actions(request)
        # Anyone who can see a document may open it (subject to the per-open
        # shielding check); only leads/superusers may bulk-export.
        if not (request.user.is_superuser or max_role_rank(request.user) >= LEAD):
            actions.pop("export_encrypted_zip", None)
        return actions

    def get_queryset(self, request):
        # Department-scope case-linked docs, but also include person-level docs
        # (no case yet, e.g. onboarding) which have no department to scope by.
        qs = admin.ModelAdmin.get_queryset(self, request)
        if request.user.is_superuser:
            return qs
        depts = user_department_ids(request.user)
        return qs.filter(
            Q(case__owner_department_id__in=depts) | Q(case__isnull=True)
        )


@admin.register(FollowUp)
class FollowUpAdmin(ScopedAdmin):
    department_path = "case__owner_department_id"
    list_display = ("case", "what", "due_date", "assignee", "done")
    list_filter = ("done", "due_date")


@admin.register(CaseLog)
class CaseLogAdmin(ScopedAdmin):
    department_path = "case__owner_department_id"
    list_display = ("case", "kind", "author", "created_at")
    list_filter = ("kind",)
    search_fields = ("case__ref", "text")

    # Append-only: entries can be added, never edited or deleted.
    def has_change_permission(self, request, obj=None):
        return obj is None  # allow the list/add views, block editing a saved entry

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CaseAssignment)
class CaseAssignmentAdmin(ScopedAdmin):
    department_path = "case__owner_department_id"
    edit_role = LEAD   # reassigning / handing off is a lead action
    list_display = ("case", "worker", "role", "active", "assigned_at")
    list_filter = ("role", "active")
    autocomplete_fields = ("worker",)


@admin.register(ExportEvent)
class ExportEventAdmin(admin.ModelAdmin):
    """Append-only audit trail of document exports. Created only by the export
    flow; never editable or deletable. Superusers see every export; others see
    only their own."""
    list_display = ("created_at", "exported_by", "document_count", "reason", "sha256")
    list_filter = ("created_at", "exported_by")
    search_fields = ("reason", "sha256", "exported_by__username")
    readonly_fields = ("exported_by", "created_at", "document_count", "reason", "manifest", "sha256")

    def has_add_permission(self, request):
        return False  # exports are recorded by the export flow, not added by hand

    def has_change_permission(self, request, obj=None):
        return obj is None

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(exported_by=request.user)


@admin.register(DocumentAccessEvent)
class DocumentAccessEventAdmin(admin.ModelAdmin):
    """Append-only audit trail of document opens (and blocked attempts). Created
    only by the open/export flow; never editable or deletable. Superusers see
    all; others see only their own."""
    list_display = ("created_at", "opened_by", "action", "allowed", "document_label", "person", "reason")
    list_filter = ("action", "allowed", "created_at")
    search_fields = ("document_label", "reason", "opened_by__username")
    readonly_fields = ("opened_by", "document", "document_label", "person", "action", "allowed", "reason", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return obj is None

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(opened_by=request.user)


@admin.register(CalendarEvent)
class CalendarEventAdmin(admin.ModelAdmin):
    """Owner-scoped, not department-scoped: a worker sees their own calendar."""
    list_display = ("start", "end", "title", "owner", "case")
    list_filter = ("owner",)
    autocomplete_fields = ("case",)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(owner=request.user)


@admin.register(LegalReference)
class LegalReferenceAdmin(admin.ModelAdmin):
    """Shared catalog of legal provisions — not department-scoped; the law is
    the same for everyone. Searchable so it autocompletes on the case page."""
    list_display = ("title", "identifier", "url", "effective_from", "repealed_on", "fetched_at")
    list_filter = ("repealed_on",)
    search_fields = ("title", "identifier", "url")


class CategoryRuleInline(admin.TabularInline):
    """Base regulations for a case category — curated here, surfaced on cases."""
    model = RegulationRule
    fk_name = "category"
    extra = 1
    autocomplete_fields = ("reference",)


class CircumstanceRuleInline(admin.TabularInline):
    """Regulations that a circumstance (e.g. disability) adds on top."""
    model = RegulationRule
    fk_name = "circumstance"
    extra = 1
    autocomplete_fields = ("reference",)


@admin.register(CaseCategory)
class CaseCategoryAdmin(admin.ModelAdmin):
    """Controlled vocab of case types. Which types exist depends on which
    department uses them, so the list (and the autocomplete picker on the case
    form, which routes through this get_queryset) is scoped to the user's
    departments plus global categories. Superusers manage the whole catalog."""
    list_display = ("name", "code", "active")
    list_filter = ("active", "departments")
    search_fields = ("name", "code")
    filter_horizontal = ("departments",)
    inlines = [CategoryRuleInline]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        depts = user_department_ids(request.user)
        return qs.filter(
            Q(departments__in=depts) | Q(departments__isnull=True)
        ).distinct()


@admin.register(Circumstance)
class CircumstanceAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "active")
    search_fields = ("name", "code")
    inlines = [CircumstanceRuleInline]


@admin.register(RegulationRule)
class RegulationRuleAdmin(admin.ModelAdmin):
    list_display = ("trigger", "reference", "level", "active", "last_reviewed")
    list_filter = ("level", "active")
    autocomplete_fields = ("category", "circumstance", "reference")
