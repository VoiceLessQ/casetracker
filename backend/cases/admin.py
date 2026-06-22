# CaseTracker — municipal case-status overlay
# Copyright 2026 VoiceLessQ — https://github.com/VoiceLessQ
# Licensed under the Apache License 2.0; see LICENSE and NOTICE.
import base64
import uuid

from django.conf import settings
from django.contrib import admin, messages
from django.db.models import Q
from django.http import FileResponse
from django.template.response import TemplateResponse
from django.utils import timezone
from django.utils.html import format_html, format_html_join

from org.models import Department, Membership
from people.access import can_open_person_documents, visible_persons
from people.admin import CprSearchMixin

from .activity import log_document_activity
from .closure import ClosureError, close_case, reopen_case
from .escalation import EscalationError, acknowledge_flag, resolve_flag
from .exports import build_encrypted_zip, generate_password, safe_drive_path
from .handoff import HandoffError, approve_handoff, reject_handoff, handoff_blockers
from .journal import JournalError, journalize
from .models import (
    Case, CaseLocator, CaseLink, StatusEvent, Document, FollowUp,
    CaseLog, CaseAssignment, CalendarEvent,
    LegalReference, CaseLegalRef,
    CaseCategory, RegulationRule, Circumstance, ExportEvent, DocumentAccessEvent,
    CaseHandoff, CaseFlag, DocumentType, DocumentActivity,
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
        cap = getattr(settings, "EXPORT_MAX_DOCUMENTS", 50)
        if not reason:
            modeladmin.message_user(request, "A reason is required to export.", messages.ERROR)
        elif not allowed_docs:
            modeladmin.message_user(
                request,
                "All selected documents belong to shielded persons you have no "
                "access to. Nothing exported; the attempts were logged.",
                messages.ERROR,
            )
        elif len(allowed_docs) > cap:
            modeladmin.message_user(
                request,
                f"Export too large: {len(allowed_docs)} documents exceeds the limit "
                f"of {cap}. Narrow your selection (bulk extraction is restricted).",
                messages.ERROR,
            )
        else:
            export_id = uuid.uuid4().hex[:12]
            now = timezone.now()
            watermark = (
                f"Exported by {request.user.get_username()} (user id {request.user.id}) "
                f"at {now:%Y-%m-%d %H:%M %Z} · export {export_id}"
            )
            password = generate_password()
            zip_bytes, manifest, sha256 = build_encrypted_zip(allowed_docs, password, watermark)
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
                "filename": f"casetracker-export-{now:%Y%m%d-%H%M%S}-{request.user.get_username()}-{export_id}.zip",
            })

    return TemplateResponse(request, "admin/cases/export_confirm.html", {
        **common,
        "title": "Confirm encrypted export",
        "documents": allowed_docs,
        "denied_count": len(denied_docs),
        "selected": [str(d.pk) for d in docs],
        "action_checkbox_name": admin.helpers.ACTION_CHECKBOX_NAME,
    })


@admin.action(description="Journalize selected (assign journal number)")
def journalize_documents(modeladmin, request, queryset):
    """Register the selected documents as records on their case: assign a journal
    number + date, after which they're immutable. Needs Member+ in the case's
    department; skips drafts with no case, already-journalized docs, and
    shielded docs the user can't access."""
    done, skipped = [], []
    for doc in queryset:
        if not request.user.is_superuser:
            dept_id = doc.case.owner_department_id if doc.case_id else None
            if role_rank_in(request.user, dept_id) < MEMBER:
                skipped.append((doc, "no edit access to the case"))
                continue
        try:
            number = journalize(doc, request.user)
            done.append((doc, number))
        except JournalError as exc:
            skipped.append((doc, str(exc)))
    if done:
        modeladmin.message_user(
            request,
            "Journalized: " + ", ".join(f"{d.label} → {n}" for d, n in done),
            messages.SUCCESS,
        )
    for doc, why in skipped:
        modeladmin.message_user(request, f"Skipped “{doc.label}”: {why}", messages.WARNING)


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


class CaseLinkInline(admin.TabularInline):
    """Links from this case to others: recurrence (CONTINUES a prior, concluded
    case), duplicates, or related matters. Adding a link is navigation, not
    access — it never grants the right to open the linked case's documents."""
    model = CaseLink
    fk_name = "from_case"
    extra = 0
    autocomplete_fields = ("to_case",)
    readonly_fields = ("created_by", "created_at")


@admin.action(description="Close selected (concluded — reopenable)")
def close_cases(modeladmin, request, queryset):
    """Conclude the selected cases. Closing never deletes — it's a reversible
    state, logged with a required reason, and refuses an incomplete case unless a
    lead overrides. Goes through a confirm page so the reason is captured."""
    cases = list(queryset)
    common = {
        **modeladmin.admin_site.each_context(request),
        "opts": modeladmin.model._meta,
        "media": modeladmin.media,
    }
    if request.POST.get("confirm"):
        reason = (request.POST.get("reason") or "").strip()
        override = bool(request.POST.get("override"))
        if reason:
            for case in cases:
                if not request.user.is_superuser:
                    rank = role_rank_in(request.user, case.owner_department_id)
                    if rank < MEMBER:
                        modeladmin.message_user(request, f"{case.ref}: no edit access.", messages.WARNING)
                        continue
                    if override and rank < LEAD:
                        modeladmin.message_user(
                            request, f"{case.ref}: only a lead can override an incomplete close.",
                            messages.ERROR,
                        )
                        continue
                try:
                    close_case(case, request.user, reason, override=override)
                except ClosureError as exc:
                    modeladmin.message_user(request, f"{case.ref}: {exc}", messages.ERROR)
                else:
                    modeladmin.message_user(request, f"{case.ref}: closed.", messages.SUCCESS)
            return None
        modeladmin.message_user(request, "A reason is required to close a case.", messages.ERROR)
    return TemplateResponse(request, "admin/cases/close_confirm.html", {
        **common,
        "title": "Close selected cases",
        "cases": cases,
        "selected": [str(c.pk) for c in cases],
        "action_checkbox_name": admin.helpers.ACTION_CHECKBOX_NAME,
    })


@admin.action(description="Reopen selected (revive a closed case)")
def reopen_cases(modeladmin, request, queryset):
    """Revive closed cases — back to In progress, logged with a required reason.
    Only cases currently Closed are reopened (a Done case re-opens via handoff)."""
    cases = list(queryset)
    common = {
        **modeladmin.admin_site.each_context(request),
        "opts": modeladmin.model._meta,
        "media": modeladmin.media,
    }
    if request.POST.get("confirm"):
        reason = (request.POST.get("reason") or "").strip()
        if reason:
            for case in cases:
                if not request.user.is_superuser and role_rank_in(request.user, case.owner_department_id) < MEMBER:
                    modeladmin.message_user(request, f"{case.ref}: no edit access.", messages.WARNING)
                    continue
                try:
                    reopen_case(case, request.user, reason)
                except ClosureError as exc:
                    modeladmin.message_user(request, f"{case.ref}: {exc}", messages.ERROR)
                else:
                    modeladmin.message_user(request, f"{case.ref}: reopened.", messages.SUCCESS)
            return None
        modeladmin.message_user(request, "A reason is required to reopen a case.", messages.ERROR)
    return TemplateResponse(request, "admin/cases/reopen_confirm.html", {
        **common,
        "title": "Reopen selected cases",
        "cases": cases,
        "selected": [str(c.pk) for c in cases],
        "action_checkbox_name": admin.helpers.ACTION_CHECKBOX_NAME,
    })


@admin.register(Case)
class CaseAdmin(CprSearchMixin, ScopedAdmin):
    department_path = "owner_department_id"
    actions = [close_cases, reopen_cases]
    cpr_bidx_paths = ("person__cpr_bidx",)
    list_display = ("ref", "title", "person", "owner_department", "status", "waiting_on", "mute_pings", "review_after", "updated_at")
    list_filter = ("owner_department", "status", "category", "mute_pings")
    search_fields = ("ref", "title", "person__name")  # CPR matched via blind index
    autocomplete_fields = ("person", "category")
    filter_horizontal = ("circumstances",)   # user-friendly dual-list selector
    inlines = [CaseAssignmentInline, FollowUpInline, CaseLegalRefInline, CaseLinkInline]
    readonly_fields = ("activity", "outstanding", "lineage", "journal")

    @admin.display(description="Activity")
    def activity(self, obj):
        """Last-updated cue so a worker sees a case drifting toward cold before
        it actually is. 'Cold' uses the canonical stale() rule; quiet-on-purpose
        (waiting / muted / explained quiet window) is shown so it isn't mistaken
        for neglect."""
        if obj is None or obj.pk is None:
            return "—"
        last = timezone.localdate(obj.updated_at)
        days = (timezone.localdate() - last).days
        base = format_html("Last updated {} ({} days ago)", last, days)
        if Case.objects.stale().filter(pk=obj.pk).exists():
            return format_html('{} — <strong style="color:#ba2121;">COLD — unexplained silence</strong>', base)
        quiet_on_purpose = (
            obj.mute_pings or obj.status in (Case.Status.WAITING, Case.Status.CLOSED)
            or bool(obj.quiet_reason)
            or (obj.review_after and obj.review_after >= timezone.localdate())
        )
        if quiet_on_purpose:
            return format_html("{} — quiet on purpose", base)
        if days >= 30:   # past half the 60-day cold window, and nobody has explained it
            return format_html('{} — <span style="color:#a06000;">approaching cold</span>', base)
        return base

    @admin.display(description="Outstanding before handoff / close")
    def outstanding(self, obj):
        """What's still missing before this case is complete enough to move on —
        required category, person, legal basis, and required document types."""
        if obj is None or obj.pk is None:
            return "Save the case first."
        blockers = handoff_blockers(obj)
        if not blockers:
            return "Complete — ready to hand off."
        return format_html(
            '<ul style="margin:0;color:#ba2121;">{}</ul>',
            format_html_join("", "<li>{}</li>", ((b,) for b in blockers)),
        )

    @admin.display(description="Recurrence / linked cases")
    def lineage(self, obj):
        """Where this case sits in a recurring matter (the CONTINUES chain),
        plus any looser links. Makes 'this has come round before' visible at a
        glance without opening anything."""
        if obj is None or obj.pk is None:
            return "Save the case first."
        n, total = obj.occurrence()
        rows = []
        if total > 1:
            chain = obj.continues_lineage()
            refs = format_html_join(" → ", "{}", (
                (format_html("<strong>{}</strong>", c.ref) if c.pk == obj.pk else c.ref,)
                for c in chain
            ))
            rows.append(format_html("Occurrence <strong>{}</strong> of {} — {}", n, total, refs))
        else:
            rows.append("Stands alone (occurrence 1 of 1).")
        others = list(obj.links_out.exclude(kind=CaseLink.Kind.CONTINUES).select_related("to_case"))
        others += list(obj.links_in.filter(kind=CaseLink.Kind.DUPLICATE).select_related("from_case"))
        for link in others:
            other = link.to_case if link.from_case_id == obj.pk else link.from_case
            rows.append(format_html("{} {}", link.get_kind_display().lower(), other.ref))
        return format_html_join("", "<div>{}</div>", ((r,) for r in rows))

    @admin.display(description="Case journal (chronological record)")
    def journal(self, obj):
        """Read-only merged record: journalized documents + status handoffs +
        narrative log entries, oldest first. The journalized documents are the
        formal record; logs/handoffs give the surrounding history."""
        if obj is None or obj.pk is None:
            return "Save the case first to see its journal."
        rows = []
        for d in obj.documents.filter(journalized_at__isnull=False):
            rows.append((d.journalized_at, format_html(
                "<strong>{}</strong> · {} · {} <em>{}</em>",
                d.journal_number, d.get_direction_display(), d.label, d.get_kind_display(),
            )))
        for e in obj.events.all():
            change = []
            if e.to_status:
                change.append(f"status → {e.to_status}")
            if e.to_department_id:
                change.append(f"dept → {e.to_department}")
            rows.append((e.timestamp, format_html(
                "handoff: {} {}", ", ".join(change) or "—", f"({e.actor})",
            )))
        for log in obj.logs.all():
            rows.append((log.created_at, format_html(
                "{}: {} <em>({})</em>", log.get_kind_display(), log.text, log.author,
            )))
        for a in obj.document_activities.all():
            rows.append((a.created_at, format_html(
                "document {}: {} {} <em>({})</em>",
                a.action, a.document_label, f"· {a.detail}" if a.detail else "", a.actor,
            )))
        if not rows:
            return "No journalized documents or log entries yet."
        rows.sort(key=lambda r: r[0])
        items = format_html_join(
            "", "<li>{} — {}</li>",
            ((r[0].strftime("%Y-%m-%d %H:%M"), r[1]) for r in rows),
        )
        return format_html('<ol style="margin:0;padding-left:1.2em;">{}</ol>', items)

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

    def get_readonly_fields(self, request, obj=None):
        ro = list(super().get_readonly_fields(request, obj))
        # Once a case exists, non-superusers can't move it between departments by
        # editing the field directly — that goes through an approved handoff.
        if obj is not None and not request.user.is_superuser:
            ro.append("owner_department")
        return tuple(dict.fromkeys(ro))

    def save_formset(self, request, form, formset, change):
        # Stamp the author on new case-to-case links; everything else saves as usual.
        instances = formset.save(commit=False)
        for obj in instances:
            if isinstance(obj, CaseLink) and obj.created_by_id is None:
                obj.created_by = request.user
            obj.save()
        formset.save_m2m()
        for obj in formset.deleted_objects:
            obj.delete()

    def save_model(self, request, obj, form, change):
        prev = None
        if change and obj.pk:
            prev = Case.objects.filter(pk=obj.pk).values("owner_department_id", "status").first()
        # Closing / reopening is a logged, reason-bearing action — not a silent
        # dropdown edit. Block the to/from-CLOSED transition here and point at the
        # action; any other field edits on the form still save.
        if prev and prev["status"] != obj.status and Case.Status.CLOSED in (prev["status"], obj.status):
            obj.status = prev["status"]
            self.message_user(
                request,
                "Use the “Close selected” / “Reopen selected” action to change a case "
                "to or from Closed — it records the required reason.",
                messages.WARNING,
            )
        super().save_model(request, obj, form, change)
        if prev:
            dept_changed = prev["owner_department_id"] != obj.owner_department_id
            status_changed = prev["status"] != obj.status
            if dept_changed or status_changed:
                # Auto-record the handoff/transition so "where is it / where has
                # it been" is a trustworthy trail.
                StatusEvent.objects.create(
                    case=obj, actor=request.user,
                    from_status=prev["status"], to_status=obj.status,
                    from_department_id=prev["owner_department_id"],
                    to_department_id=obj.owner_department_id,
                    note="Changed via case edit",
                )


@admin.register(StatusEvent)
class StatusEventAdmin(ScopedAdmin):
    department_path = "case__owner_department_id"
    list_display = ("case", "timestamp", "actor", "from_status", "to_status")
    list_filter = ("to_status",)


@admin.register(Document)
class DocumentAdmin(CprSearchMixin, ScopedAdmin):
    department_path = "case__owner_department_id"
    cpr_bidx_paths = ("person__cpr_bidx",)
    list_display = ("label", "document_type", "journal_number", "direction", "kind", "case", "person", "journalized_at", "added_by", "added_at")
    list_filter = ("direction", "kind", "source", "document_type")
    search_fields = ("label", "journal_number", "case__ref", "location", "email_from", "email_subject")  # CPR via blind index
    actions = [open_document, export_encrypted_zip, journalize_documents]

    # Journalized documents are records: their journal fields are always read-only,
    # and once journalized the content fields lock too (corrections go in new
    # entries, never by rewriting the record).
    _journal_fields = ("journal_number", "journal_sequence", "journalized_at", "journalized_by")
    _content_fields = ("kind", "direction", "document_type", "label", "location", "source", "case", "person")

    def get_readonly_fields(self, request, obj=None):
        ro = list(super().get_readonly_fields(request, obj)) + list(self._journal_fields)
        if obj is not None and obj.is_journalized:
            ro += list(self._content_fields)
        return tuple(dict.fromkeys(ro))

    def get_actions(self, request):
        actions = super().get_actions(request)
        # Anyone who can see a document may open it (subject to the per-open
        # shielding check); only leads/superusers may bulk-export.
        if not (request.user.is_superuser or max_role_rank(request.user) >= LEAD):
            actions.pop("export_encrypted_zip", None)
        # Journalizing requires edit (Member+) rights somewhere.
        if not (request.user.is_superuser or max_role_rank(request.user) >= MEMBER):
            actions.pop("journalize_documents", None)
        return actions

    def has_delete_permission(self, request, obj=None):
        # A journalized document is a record and cannot be deleted by anyone.
        if obj is not None and obj.is_journalized:
            return False
        return super().has_delete_permission(request, obj)

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

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        # Nothing enters or changes silently: every add/edit hits the trail.
        log_document_activity(
            request.user, obj, DocumentActivity.Action.EDITED if change else DocumentActivity.Action.ADDED
        )

    def delete_model(self, request, obj):
        log_document_activity(request.user, obj, DocumentActivity.Action.REMOVED)
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        for obj in queryset:
            log_document_activity(request.user, obj, DocumentActivity.Action.REMOVED)
        super().delete_queryset(request, queryset)


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


@admin.action(description="Approve selected handoffs (move the case)")
def approve_handoffs(modeladmin, request, queryset):
    for handoff in queryset:
        try:
            approve_handoff(handoff, request.user)
        except HandoffError as exc:
            modeladmin.message_user(request, f"{handoff.case.ref}: {exc}", messages.ERROR)
        else:
            modeladmin.message_user(
                request, f"{handoff.case.ref}: moved to {handoff.to_department}.", messages.SUCCESS
            )


@admin.action(description="Reject selected handoffs")
def reject_handoffs(modeladmin, request, queryset):
    for handoff in queryset:
        try:
            reject_handoff(handoff, request.user)
        except HandoffError as exc:
            modeladmin.message_user(request, f"{handoff.case.ref}: {exc}", messages.ERROR)
        else:
            modeladmin.message_user(request, f"{handoff.case.ref}: handoff rejected.", messages.WARNING)


@admin.register(CaseHandoff)
class CaseHandoffAdmin(ScopedAdmin):
    department_path = "case__owner_department_id"
    list_display = ("case", "from_department", "to_department", "status", "requested_by", "requested_at", "decided_by")
    list_filter = ("status",)
    search_fields = ("case__ref", "note")
    autocomplete_fields = ("case", "to_department")
    actions = [approve_handoffs, reject_handoffs]

    _stamped = ("from_department", "requested_by", "requested_at", "status",
                "decided_by", "decided_at", "decision_note")

    def get_readonly_fields(self, request, obj=None):
        ro = list(super().get_readonly_fields(request, obj)) + list(self._stamped)
        if obj is not None:
            # A created handoff is a record decided via the approve/reject
            # actions, not by editing it.
            ro += ["case", "to_department", "note"]
        return tuple(dict.fromkeys(ro))

    def save_model(self, request, obj, form, change):
        if not change:
            obj.requested_by = request.user
            obj.from_department = obj.case.owner_department
            obj.status = CaseHandoff.Status.PENDING
        super().save_model(request, obj, form, change)


@admin.action(description="Acknowledge selected flags (mark as seen)")
def acknowledge_flags(modeladmin, request, queryset):
    for flag in queryset:
        acknowledge_flag(flag, request.user)
        modeladmin.message_user(request, f"{flag.case.ref}: flag acknowledged.", messages.INFO)


@admin.action(description="Resolve selected flags")
def resolve_flags(modeladmin, request, queryset):
    for flag in queryset:
        try:
            resolve_flag(flag, request.user)
        except EscalationError as exc:
            modeladmin.message_user(request, f"{flag.case.ref}: {exc}", messages.ERROR)
        else:
            modeladmin.message_user(request, f"{flag.case.ref}: flag resolved.", messages.SUCCESS)


@admin.register(CaseFlag)
class CaseFlagAdmin(ScopedAdmin):
    department_path = "case__owner_department_id"
    list_display = ("case", "kind", "status", "to_department", "raised_by", "created_at", "resolved_by")
    list_filter = ("status", "kind")
    search_fields = ("case__ref", "text")
    autocomplete_fields = ("case", "to_department")
    actions = [acknowledge_flags, resolve_flags]

    _stamped = ("raised_by", "created_at", "status", "resolved_by", "resolved_at", "resolution_note")

    def get_queryset(self, request):
        # A flag is visible to the holding department AND the department it was
        # addressed to, so "notify the other department" actually reaches them —
        # ScopedAdmin only scopes by the holder, so widen it here.
        qs = admin.ModelAdmin.get_queryset(self, request)
        if request.user.is_superuser:
            return qs
        depts = list(user_department_ids(request.user))
        return qs.filter(
            Q(case__owner_department_id__in=depts) | Q(to_department_id__in=depts)
        )

    def get_readonly_fields(self, request, obj=None):
        ro = list(super().get_readonly_fields(request, obj)) + list(self._stamped)
        if obj is not None:
            # The flag's reason is fixed at raise time; it moves along via the
            # acknowledge/resolve actions, not by editing it.
            ro += ["case", "kind", "to_department", "text"]
        return tuple(dict.fromkeys(ro))

    def save_model(self, request, obj, form, change):
        if not change:
            obj.raised_by = request.user
            if obj.to_department_id is None:
                obj.to_department = obj.case.owner_department
            obj.status = CaseFlag.Status.OPEN
        super().save_model(request, obj, form, change)


@admin.register(CaseLocator)
class CaseLocatorAdmin(admin.ModelAdmin):
    """The 'where is this case' tab: a read-only, cross-department locator.

    Deliberately NOT a ScopedAdmin — its whole point is to see across
    departments so a department doesn't go blind once a case leaves it. The
    expansion is held in check two ways: it is gated on the `can_locate`
    permission (default-deny), and its person scope runs through
    `visible_persons` so shielding stays existence-hidden. It exposes only
    routing metadata — never documents, notes, or a person's protected fields.
    """

    list_display = ("ref", "title", "person_display", "owner_department", "status", "open_flags", "last_moved")
    list_filter = ("status", "owner_department")
    search_fields = ("ref", "title", "person__name")
    list_display_links = None          # a directory, never a door into the record
    list_select_related = ("person", "owner_department")

    # Read-only: locate, never edit or open.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def _can_locate(self, request):
        return request.user.is_superuser or request.user.has_perm("cases.can_locate")

    def has_view_permission(self, request, obj=None):
        return self._can_locate(request)

    def has_module_permission(self, request):
        return self._can_locate(request)

    def get_queryset(self, request):
        # Cross-department on purpose; shielding stays existence-hidden via
        # visible_persons. Cases with no person have nothing to shield.
        qs = Case.objects.all().select_related("person", "owner_department")
        if request.user.is_superuser:
            return qs
        vis = visible_persons(request.user)
        return qs.filter(Q(person__isnull=True) | Q(person__in=vis))

    def get_search_results(self, request, queryset, search_term):
        # ref / title / name handled by search_fields; add EXACT CPR via the
        # blind index (the same path Person.lookup uses), then re-apply the
        # visibility filter so the CPR branch can't leak a shielded record.
        qs, distinct = super().get_search_results(request, queryset, search_term)
        term = (search_term or "").strip()
        if term:
            from people import crypto

            bidx = crypto.blind_index_for_term(term)
            if bidx:
                hit_ids = self.get_queryset(request).filter(person__cpr_bidx=bidx).values("pk")
                qs = (qs | self.model.objects.filter(pk__in=hit_ids)).distinct()
            self._log_locate(request, term, qs)
        return qs, distinct

    def _log_locate(self, request, term, qs):
        # Search is access — record it, same stance as person search. On a hit
        # we log the people reached (not the term); on a miss, the term.
        from people.access import log_search
        from people.models import SearchEvent

        if getattr(request, "_locate_logged", False):
            return
        request._locate_logged = True
        persons = [c.person for c in qs[:50] if c.person_id]
        log_search(request.user, term, persons, SearchEvent.Kind.NAME)

    @admin.display(description="Person")
    def person_display(self, obj):
        if obj.person_id is None:
            return "—"
        return f"{obj.person.name} ({obj.person.masked_cpr})"

    @admin.display(description="Open flags")
    def open_flags(self, obj):
        labels = dict(CaseFlag.Kind.choices)
        kinds = obj.flags.filter(status=CaseFlag.Status.OPEN).values_list("kind", flat=True)
        return ", ".join(labels.get(k, k) for k in kinds) or "—"

    @admin.display(description="Last moved")
    def last_moved(self, obj):
        e = obj.events.filter(to_department__isnull=False).order_by("-timestamp").first()
        return f"{e.timestamp:%Y-%m-%d} → {e.to_department}" if e else "—"


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


@admin.register(DocumentActivity)
class DocumentActivityAdmin(admin.ModelAdmin):
    """Append-only trail of document add/edit/journalize/remove. Created by the
    document flows; never editable or deletable. Superusers see all; others see
    their own."""
    list_display = ("created_at", "actor", "action", "document_label", "case", "person", "detail")
    list_filter = ("action", "created_at")
    search_fields = ("document_label", "detail", "actor__username", "case__ref")
    readonly_fields = ("actor", "action", "document", "document_label", "case", "person", "detail", "created_at")

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
        return qs.filter(actor=request.user)


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
    filter_horizontal = ("departments", "required_document_types")
    inlines = [CategoryRuleInline]


@admin.register(DocumentType)
class DocumentTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "active")
    list_filter = ("active",)
    search_fields = ("name", "code")

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
