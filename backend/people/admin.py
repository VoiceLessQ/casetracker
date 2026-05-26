from django.contrib import admin
from django.db.models import Q

from cases.models import Document
from .crypto import blind_index_for_term
from .models import Person, Relationship, PersonNote


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


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = ("name", "cpr", "address", "birth_date")
    # CPR is encrypted, so it isn't an icontains field; lookup() matches it via
    # the blind index. Keep name/address here for the search box and autocomplete.
    search_fields = ("name", "address")
    inlines = [RelationshipInline, PersonNoteInline, PersonDocumentInline]

    def get_search_results(self, request, queryset, search_term):
        # Use the dash-tolerant lookup() everywhere this admin is searched —
        # including the parent-picker autocomplete on the relationship inline.
        if search_term:
            return queryset.lookup(search_term), False
        return queryset, False


@admin.register(PersonNote)
class PersonNoteAdmin(CprSearchMixin, admin.ModelAdmin):
    cpr_bidx_paths = ("person__cpr_bidx",)
    list_display = ("person", "created_at", "visibility", "author")
    list_filter = ("visibility",)
    search_fields = ("person__name", "text")   # CPR handled via blind index

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
