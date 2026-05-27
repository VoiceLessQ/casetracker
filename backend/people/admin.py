# CaseTracker — municipal case-status overlay
# Copyright 2026 VoiceLessQ — https://github.com/VoiceLessQ
# Licensed under the Apache License 2.0; see LICENSE and NOTICE.
from django.contrib import admin
from django.db.models import Q
from django.template.response import TemplateResponse
from django.urls import path

from cases.models import Document
from .access import log_search, searchable_persons, visible_persons
from .crypto import blind_index_for_term
from .models import Person, Relationship, PersonNote, PersonAccessGrant, SearchEvent


def _search_kind(term):
    return SearchEvent.Kind.CPR if blind_index_for_term(term) else SearchEvent.Kind.NAME


class CprSearchMixin:
    """Restores CPR search on admins that key off a person's CPR, now that the
    CPR is encrypted. If the search term looks like a CPR it is matched via the
    blind index (exact, dash-tolerant); other terms fall through to the normal
    search fields. Set `cpr_bidx_paths` to the lookup path(s) to the person's
    cpr_bidx from this model."""

    cpr_bidx_paths = ()

    def get_search_results(self, request, queryset, search_term):
        qs, distinct = super().get_search_results(request, queryset, search_term)
        bidx = blind_index_for_term(search_term)
        if bidx and self.cpr_bidx_paths:
            cond = Q()
            for path in self.cpr_bidx_paths:
                cond |= Q(**{path: bidx})
            qs = (qs | queryset.filter(cond)).distinct()
            distinct = True
        return qs, distinct


class RelationshipInline(admin.TabularInline):
    """Edit a person's family edges inline. To end one (parent died, adoption),
    set ended_on + reason — never delete it."""
    model = Relationship
    fk_name = "person"
    extra = 1
    autocomplete_fields = ("relative",)
    fields = ("relative", "relation", "started_on", "ended_on", "ended_reason")


class PersonNoteInline(admin.TabularInline):
    """Running notes on the person, newest first. Add-only here (no delete);
    the dedicated admin enforces full append-only."""
    model = PersonNote
    extra = 1
    can_delete = False
    fields = ("text", "visibility", "author", "created_at")
    readonly_fields = ("created_at",)


class PersonDocumentInline(admin.TabularInline):
    """Document links on the person — attach documents (and captured emails)
    during onboarding before any case exists, and see them on the person page."""
    model = Document
    fk_name = "person"
    extra = 1
    fields = ("kind", "label", "location", "source", "email_from", "email_sent_at", "case", "added_by")
    autocomplete_fields = ("case",)


class PersonAccessGrantInline(admin.TabularInline):
    """Grant a user permission to OPEN this (shielded) person's documents.
    granted_by is stamped automatically. Revoke by setting an expiry date."""
    model = PersonAccessGrant
    extra = 0
    fields = ("user", "reason", "expires_on", "granted_by", "created_at")
    readonly_fields = ("granted_by", "created_at")
    autocomplete_fields = ("user",)


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = ("name", "cpr", "address", "birth_date", "is_shielded")
    list_filter = ("is_shielded",)
    # CPR/address are encrypted, so they aren't icontains fields. lookup()
    # matches name (partial) and CPR (exact, via blind index).
    search_fields = ("name",)
    inlines = [RelationshipInline, PersonNoteInline, PersonDocumentInline, PersonAccessGrantInline]

    def get_queryset(self, request):
        # Search/browse is scoped to "people you have a reason to reach": your
        # departments' case subjects, plus the whole base for intake/superusers,
        # and never shielded-without-grant. Browsing the entire citizen base is
        # the snooping hole this closes.
        return searchable_persons(request.user)

    def get_search_results(self, request, queryset, search_term):
        results = queryset.lookup(search_term) if search_term else queryset
        # Search is access: log explicit searches (not autocomplete typeahead,
        # which would flood the log). uid-on-hit / term-on-miss handled by log_search.
        if search_term and not request.path.rstrip("/").endswith("autocomplete"):
            log_search(request.user, search_term, results, _search_kind(search_term))
        return results, False

    def get_urls(self):
        custom = [
            path("break-glass/", self.admin_site.admin_view(self.break_glass_view),
                 name="people_person_breakglass"),
        ]
        return custom + super().get_urls()

    def break_glass_view(self, request):
        """Reach a person outside your scope — deliberate, reason-required, and
        logged loudly. Never bypasses shielding (still limited to visible_persons)."""
        q = (request.POST.get("q") or "").strip()
        reason = (request.POST.get("reason") or "").strip()
        results, done = [], False
        if request.method == "POST" and q and reason:
            results = list(visible_persons(request.user).lookup(q)[:50])
            log_search(request.user, q, results, _search_kind(q), break_glass=True, reason=reason)
            done = True
        return TemplateResponse(request, "admin/people/break_glass.html", {
            **self.admin_site.each_context(request),
            "title": "Break-the-glass person search",
            "opts": self.model._meta, "results": results, "q": q, "reason": reason, "done": done,
        })

    def save_formset(self, request, form, formset, change):
        # Stamp granted_by on new access grants created via the inline.
        instances = formset.save(commit=False)
        for obj in instances:
            if isinstance(obj, PersonAccessGrant) and not obj.granted_by_id:
                obj.granted_by = request.user
            obj.save()
        formset.save_m2m()
        for obj in formset.deleted_objects:
            obj.delete()


@admin.register(PersonNote)
class PersonNoteAdmin(CprSearchMixin, admin.ModelAdmin):
    cpr_bidx_paths = ("person__cpr_bidx",)
    list_display = ("person", "created_at", "visibility", "author")
    list_filter = ("visibility",)
    search_fields = ("person__name",)   # text is encrypted; CPR via blind index

    # Append-only: notes can be added, never edited or deleted.
    def has_change_permission(self, request, obj=None):
        return obj is None

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Relationship)
class RelationshipAdmin(CprSearchMixin, admin.ModelAdmin):
    cpr_bidx_paths = ("person__cpr_bidx", "relative__cpr_bidx")
    list_display = ("person", "relation", "relative", "ended_on", "ended_reason")
    list_filter = ("relation", "ended_reason")
    search_fields = ("person__name", "relative__name")   # CPR via blind index
    autocomplete_fields = ("person", "relative")


@admin.register(PersonAccessGrant)
class PersonAccessGrantAdmin(admin.ModelAdmin):
    """Who may open which shielded person's documents. granted_by is stamped
    automatically; revoke by setting an expiry date (kept as history)."""
    list_display = ("person", "user", "granted_by", "created_at", "expires_on")
    list_filter = ("created_at", "expires_on")
    search_fields = ("person__name", "user__username", "reason")
    autocomplete_fields = ("person", "user")
    readonly_fields = ("granted_by", "created_at")

    def save_model(self, request, obj, form, change):
        if not obj.granted_by_id:
            obj.granted_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(SearchEvent)
class SearchEventAdmin(admin.ModelAdmin):
    """Oversight review of who searched for whom — append-only, superuser-only.
    This log is itself a sensitive PII surface, so it's restricted to oversight;
    filter on break_glass to see the loud exceptions first."""
    list_display = ("created_at", "actor", "kind", "break_glass", "result_count", "term", "reason")
    list_filter = ("break_glass", "kind", "created_at")
    search_fields = ("actor__username", "term", "reason")
    readonly_fields = ("actor", "kind", "term", "matched", "result_count", "break_glass", "reason", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False   # a log, never edited; viewing is via has_view_permission

    def has_delete_permission(self, request, obj=None):
        return False

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser
