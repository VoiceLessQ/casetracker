from django.contrib import admin
from django.db.models import Q

from .models import (
    Case, StatusEvent, Document, FollowUp,
    CaseLog, CaseAssignment, CalendarEvent,
    LegalReference, CaseLegalRef,
    CaseCategory, RegulationRule, Circumstance,
)


def user_department_ids(user):
    """The department ids this user belongs to."""
    return user.memberships.values_list("department_id", flat=True)


class ScopedAdmin(admin.ModelAdmin):
    """Base admin that limits visible rows to the user's departments.
    Superusers see everything. Each subclass sets `department_path` to the
    ORM lookup that reaches the owning department from that model."""

    department_path = "owner_department_id"

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(
            **{f"{self.department_path}__in": user_department_ids(request.user)}
        )


class CaseAssignmentInline(admin.TabularInline):
    """Who is working this case — shown on the case page."""
    model = CaseAssignment
    extra = 1
    autocomplete_fields = ("worker",)


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
class CaseAdmin(ScopedAdmin):
    department_path = "owner_department_id"
    list_display = ("ref", "title", "person", "owner_department", "status", "waiting_on", "mute_pings", "review_after", "updated_at")
    list_filter = ("owner_department", "status", "category", "mute_pings")
    search_fields = ("ref", "title", "person__cpr", "person__name")  # search by CPR
    autocomplete_fields = ("person", "category")
    filter_horizontal = ("circumstances",)   # user-friendly dual-list selector
    inlines = [CaseAssignmentInline, FollowUpInline, CaseLegalRefInline]


@admin.register(StatusEvent)
class StatusEventAdmin(ScopedAdmin):
    department_path = "case__owner_department_id"
    list_display = ("case", "timestamp", "actor", "from_status", "to_status")
    list_filter = ("to_status",)


@admin.register(Document)
class DocumentAdmin(ScopedAdmin):
    department_path = "case__owner_department_id"
    list_display = ("label", "kind", "case", "person", "email_from", "source", "added_by", "added_at")
    list_filter = ("kind", "source")
    search_fields = ("label", "case__ref", "person__cpr", "location", "email_from", "email_subject")

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
    list_display = ("case", "worker", "role", "active", "assigned_at")
    list_filter = ("role", "active")
    autocomplete_fields = ("worker",)


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
    list_display = ("name", "code", "active")
    search_fields = ("name", "code")
    inlines = [CategoryRuleInline]


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
