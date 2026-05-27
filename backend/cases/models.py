# CaseTracker — municipal case-status overlay
# Copyright 2026 VoiceLessQ — https://github.com/VoiceLessQ
# Licensed under the Apache License 2.0; see LICENSE and NOTICE.
from django.conf import settings
from django.db import models

from org.models import Department


class CaseQuerySet(models.QuerySet):
    def stale(self, days=60, on=None):
        """Cases that have gone UNEXPLAINED-silent: no update in `days`, not
        muted, not in an explained quiet period, and not parked in WAITING
        (which is silence with a known external cause).

        Explained or muted silence is excluded on purpose — the system flags
        only silence nobody has accounted for. Pinging on top of this list is
        opt-in, never automatic.

        Note: 'last activity' is approximated here by `updated_at`. To be exact
        it should be the latest of updated_at / last StatusEvent / last CaseLog;
        that refinement is a query change, not a schema one.
        """
        from datetime import date, timedelta
        on = on or date.today()
        cutoff = on - timedelta(days=days)
        return (
            self.filter(updated_at__date__lte=cutoff, mute_pings=False)
            .exclude(status=self.model.Status.WAITING)
            .filter(models.Q(review_after__isnull=True) | models.Q(review_after__lt=on))
        )


class Case(models.Model):
    """A case, linked to the person it concerns.

    PROTOTYPE RULE: all linked personal data is PLACEHOLDER/synthetic.
    This system must never connect to a real CPR register or real records.
    """

    class Status(models.TextChoices):
        NEW = "new", "New"
        IN_PROGRESS = "in_progress", "In progress"
        WAITING = "waiting", "Waiting"          # waiting on something external
        BLOCKED = "blocked", "Blocked"
        DONE = "done", "Done"

    ref = models.CharField(max_length=64, unique=True)         # official case number
    title = models.CharField(max_length=160)                   # short label
    person = models.ForeignKey(
        "people.Person", on_delete=models.PROTECT, related_name="cases",
        null=True, blank=True,
    )
    category = models.ForeignKey(
        "CaseCategory", on_delete=models.PROTECT, related_name="cases",
        null=True, blank=True,
    )
    circumstances = models.ManyToManyField(
        "Circumstance", related_name="cases", blank=True,
    )
    legal_basis = models.CharField(max_length=64, blank=True)  # quick free-text §
    legal_refs = models.ManyToManyField(
        "LegalReference", through="CaseLegalRef", related_name="cases", blank=True,
    )
    owner_department = models.ForeignKey(
        Department, on_delete=models.PROTECT, related_name="cases",
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.NEW,
    )
    waiting_on = models.CharField(max_length=255, blank=True)  # what's blocking
    # Accounting for silence so only UNEXPLAINED silence ever surfaces:
    review_after = models.DateField(null=True, blank=True)     # quiet on purpose until this date
    quiet_reason = models.CharField(max_length=255, blank=True)  # why it's quiet, if known
    mute_pings = models.BooleanField(default=False)            # never flag/ping this case
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="cases_created",
    )
    opened_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = CaseQuerySet.as_manager()

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"{self.ref} — {self.title}"

    def applicable_rules(self, on=None):
        """Regulation rules in effect for this case: its category's base rules
        plus the rules of every selected circumstance. Excludes inactive rules
        and laws not currently in effect. This is what the system *shows* the
        caseworker; manual additions via CaseLegalRef are separate.
        """
        triggers = models.Q()
        has_trigger = False
        if self.category_id:
            triggers |= models.Q(category_id=self.category_id)
            has_trigger = True
        circ_ids = list(self.circumstances.values_list("id", flat=True))
        if circ_ids:
            triggers |= models.Q(circumstance_id__in=circ_ids)
            has_trigger = True
        if not has_trigger:
            return []
        rules = (RegulationRule.objects
                 .filter(triggers, active=True)
                 .select_related("reference", "category", "circumstance"))
        return [r for r in rules if r.reference.in_effect(on)]


class StatusEvent(models.Model):
    """Append-only history. Captures status changes AND department handoffs,
    which is the cross-department 'where did it go' trail."""

    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name="events")
    timestamp = models.DateTimeField(auto_now_add=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    from_status = models.CharField(max_length=16, blank=True)
    to_status = models.CharField(max_length=16, blank=True)
    from_department = models.ForeignKey(
        Department, on_delete=models.PROTECT, null=True, blank=True,
        related_name="+",
    )
    to_department = models.ForeignKey(
        Department, on_delete=models.PROTECT, null=True, blank=True,
        related_name="+",
    )
    note = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self):
        return f"{self.case.ref} @ {self.timestamp:%Y-%m-%d}"


class Document(models.Model):
    """A LINK to a document on the municipality's own drive.

    We do NOT store the file — only a reference to where it already is.
    'Upload-through' (optional) writes a file into the person's drive folder
    and records the resulting location here; the bytes are never kept in this
    database.

    An EMAIL is captured the same way: the email is written into the person's
    folder (upload-through) and linked here as kind=EMAIL, with from/subject/
    sent_at kept so correspondence reads as a timeline. It attaches at the
    person level so any worker on that person can stay up to date.
    """

    class Source(models.TextChoices):
        LINKED = "linked", "Linked — already on the drive"
        UPLOADED = "uploaded", "Uploaded to the drive via this system"

    class Kind(models.TextChoices):
        GENERIC = "generic", "Document"
        EMAIL = "email", "Email"

    class Direction(models.TextChoices):
        INCOMING = "in", "Incoming"        # received from outside
        OUTGOING = "out", "Outgoing"       # sent to outside
        INTERNAL = "internal", "Internal"  # internal / working document

    # Either or both may be set. A document can attach to a person before any
    # case exists (onboarding a new / newborn person), to a case, or to both.
    case = models.ForeignKey(
        Case, on_delete=models.CASCADE, related_name="documents",
        null=True, blank=True,
    )
    person = models.ForeignKey(
        "people.Person", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="documents",
    )
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.GENERIC)
    label = models.CharField(max_length=120)            # e.g. "Address certificate"
    location = models.CharField(max_length=500)         # path/URL on the drive
    source = models.CharField(
        max_length=16, choices=Source.choices, default=Source.LINKED,
    )
    direction = models.CharField(
        max_length=8, choices=Direction.choices, default=Direction.INTERNAL,
    )  # records direction: incoming / outgoing / internal correspondence
    document_type = models.ForeignKey(
        "DocumentType", on_delete=models.PROTECT, null=True, blank=True,
        related_name="documents",
    )  # controlled type; used to satisfy a category's required-document list
    # Email metadata (kind=EMAIL): kept so correspondence reads as a timeline.
    email_from = models.CharField(max_length=255, blank=True)
    email_subject = models.CharField(max_length=255, blank=True)
    email_sent_at = models.DateTimeField(null=True, blank=True)
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    added_at = models.DateTimeField(auto_now_add=True)

    # Journaling (records registration). A document starts as a DRAFT and is
    # "journalized" onto a case: it gets a permanent journal number + date and
    # becomes an immutable record. journalized_at NULL = not yet journalized.
    journal_number = models.CharField(max_length=80, blank=True, db_index=True)
    journal_sequence = models.PositiveIntegerField(null=True, blank=True)  # running no. within the case
    journalized_at = models.DateTimeField(null=True, blank=True)
    journalized_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        null=True, blank=True, related_name="documents_journalized",
    )

    class Meta:
        ordering = ["-added_at"]
        constraints = [
            models.CheckConstraint(
                name="document_has_case_or_person",
                check=models.Q(case__isnull=False) | models.Q(person__isnull=False),
            ),
            models.UniqueConstraint(
                fields=["journal_number"],
                condition=~models.Q(journal_number=""),
                name="uniq_document_journal_number",
            ),
            models.UniqueConstraint(
                fields=["case", "journal_sequence"],
                condition=models.Q(journal_sequence__isnull=False),
                name="uniq_document_case_journal_sequence",
            ),
        ]

    @property
    def is_journalized(self):
        return self.journalized_at is not None

    def __str__(self):
        tag = f"[{self.journal_number}] " if self.journal_number else ""
        return f"{tag}{self.label} → {self.location}"


class FollowUp(models.Model):
    """The 'next session / what's due' item."""

    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name="followups")
    due_date = models.DateField()
    what = models.CharField(max_length=255)       # what needs doing next
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
    )
    done = models.BooleanField(default=False)

    class Meta:
        ordering = ["done", "due_date"]

    def __str__(self):
        return f"{self.case.ref}: {self.what} (due {self.due_date})"


class CaseLog(models.Model):
    """Append-only narrative log. A caseworker records what they did; entries
    are never edited or deleted (enforced in admin) — they accumulate as the
    case's running record of who did what, when."""

    class Kind(models.TextChoices):
        NOTE = "note", "Note"
        CALL = "call", "Phone call"
        MEETING = "meeting", "Meeting"
        DECISION = "decision", "Decision"
        HANDOFF = "handoff", "Handoff"

    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name="logs")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.NOTE)
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.case.ref} · {self.get_kind_display()} · {self.created_at:%Y-%m-%d}"


class CaseAssignment(models.Model):
    """Which social workers are working a case, and in what role.
    Answers 'who is on this'."""

    class Role(models.TextChoices):
        LEAD = "lead", "Lead"
        SUPPORT = "support", "Support"
        OBSERVER = "observer", "Observer"

    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name="assignments")
    worker = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="case_assignments",
    )
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.SUPPORT)
    assigned_at = models.DateTimeField(auto_now_add=True)
    active = models.BooleanField(default=True)

    class Meta:
        unique_together = ("case", "worker")
        ordering = ["-active", "role"]

    def __str__(self):
        return f"{self.worker} · {self.get_role_display()} · {self.case.ref}"


class CalendarEvent(models.Model):
    """A worker's calendar entry, optionally tied to a case. Personal schedule
    and case appointments in one place. Answers 'when'."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="calendar_events",
    )
    case = models.ForeignKey(
        Case, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="calendar_events",
    )
    title = models.CharField(max_length=200)
    start = models.DateTimeField()
    end = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["start"]

    def __str__(self):
        return f"{self.start:%Y-%m-%d %H:%M} · {self.title}"


class LegalReference(models.Model):
    """A legal provision in Greenland's legal portal (nalunaarutit.gl) or
    similar. Reusable across cases.

    The canonical `url` is the authority — laws get amended, so it stays current.
    `snapshot_location` + `fetched_at` are an OPTIONAL dated copy; never treat a
    snapshot as the current law. (Laws are public, so caching one is fine — the
    only risk here is staleness, not privacy.)
    """

    LEGAL_PORTAL = "https://nalunaarutit.gl/?sc_lang=da"

    title = models.CharField(max_length=255)                   # e.g. the act / regulation name
    identifier = models.CharField(max_length=120, blank=True)  # the § or regulation number
    url = models.URLField(max_length=500, default=LEGAL_PORTAL)
    snapshot_location = models.CharField(max_length=500, blank=True)  # link/path to a downloaded copy
    fetched_at = models.DateField(null=True, blank=True)       # date the snapshot was taken
    effective_from = models.DateField(null=True, blank=True)   # when this law took effect
    repealed_on = models.DateField(null=True, blank=True)      # when it stopped applying (if it has)

    class Meta:
        ordering = ["title"]

    def __str__(self):
        return f"{self.title} ({self.identifier})" if self.identifier else self.title

    def in_effect(self, on=None):
        """True if this law applies on the given date (default today). Used so
        only currently-valid laws are surfaced to caseworkers."""
        from datetime import date
        on = on or date.today()
        if self.effective_from and on < self.effective_from:
            return False
        if self.repealed_on and on >= self.repealed_on:
            return False
        return True


class CaseLegalRef(models.Model):
    """Links a legal reference to a case. `required` marks a basis the case must
    keep on record (the 'remember this' part). `note` records why it applies —
    e.g. the circumstance that triggered it."""

    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name="legal_links")
    reference = models.ForeignKey(
        LegalReference, on_delete=models.PROTECT, related_name="case_links",
    )
    required = models.BooleanField(default=False)
    note = models.CharField(max_length=255, blank=True)
    auto = models.BooleanField(default=False)   # True if attached by a rule, False if added by hand
    rule = models.ForeignKey(
        "RegulationRule", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="applied_links",
    )
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("case", "reference")

    def __str__(self):
        flag = " [required]" if self.required else ""
        return f"{self.case.ref} → {self.reference}{flag}"


class CaseCategoryQuerySet(models.QuerySet):
    def for_department(self, dept):
        """Active categories a given department may use: ones explicitly linked
        to it, plus global ones (no department set = available everywhere)."""
        return (
            self.filter(active=True)
            .filter(models.Q(departments=dept) | models.Q(departments__isnull=True))
            .distinct()
        )


class CaseCategory(models.Model):
    """Controlled case type. Free text can't reliably drive the regulation map;
    a fixed vocabulary can. Each category carries the regulations that apply to
    it (via RegulationRule).

    Categories are department-scoped: which case types exist depends on which
    department uses them. Leaving `departments` empty makes a category global
    (available to every department) — for cross-cutting types like aktindsigt
    or emnesag that every department handles."""

    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=120)
    departments = models.ManyToManyField(
        Department, blank=True, related_name="case_categories",
        help_text="Departments that use this category. Leave empty to make it "
                  "available to every department.",
    )
    required_document_types = models.ManyToManyField(
        "DocumentType", blank=True, related_name="required_by_categories",
        help_text="Document types that must be attached to a case of this "
                  "category before it can be handed off.",
    )
    active = models.BooleanField(default=True)

    objects = CaseCategoryQuerySet.as_manager()

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "case categories"

    def __str__(self):
        return self.name


class RegulationRule(models.Model):
    """Maps a TRIGGER (a case category, or a circumstance such as disability) to
    a legal reference that applies. This is the 'so the caseworker doesn't have
    to remember' layer: the system surfaces every rule for the case's category
    plus its selected circumstances, instead of relying on recall.

    Exactly one of `category` / `circumstance` is set per rule.

    It is a CURATED knowledge base — only as reliable as its upkeep:
      - surface a rule only if rule.active AND reference.in_effect()
      - `last_reviewed` shows when someone last confirmed it is current; a stale
        rule gives false confidence, which is worse than no rule at all.
    """

    class Level(models.TextChoices):
        REQUIRED = "required", "Required — must be addressed"
        RECOMMENDED = "recommended", "Recommended — should be considered"

    category = models.ForeignKey(
        CaseCategory, on_delete=models.CASCADE, related_name="rules",
        null=True, blank=True,
    )
    circumstance = models.ForeignKey(
        "Circumstance", on_delete=models.CASCADE, related_name="rules",
        null=True, blank=True,
    )
    reference = models.ForeignKey(
        LegalReference, on_delete=models.PROTECT, related_name="rules",
    )
    level = models.CharField(max_length=16, choices=Level.choices, default=Level.REQUIRED)
    last_reviewed = models.DateField(null=True, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-level"]
        constraints = [
            # exactly one trigger
            models.CheckConstraint(
                name="regrule_one_trigger",
                check=(
                    models.Q(category__isnull=False, circumstance__isnull=True)
                    | models.Q(category__isnull=True, circumstance__isnull=False)
                ),
            ),
            models.UniqueConstraint(
                fields=["category", "reference"],
                condition=models.Q(category__isnull=False),
                name="uniq_category_reference",
            ),
            models.UniqueConstraint(
                fields=["circumstance", "reference"],
                condition=models.Q(circumstance__isnull=False),
                name="uniq_circumstance_reference",
            ),
        ]

    @property
    def trigger(self):
        return self.category or self.circumstance

    def __str__(self):
        return f"{self.trigger} → {self.reference} ({self.level})"


class Circumstance(models.Model):
    """A toggleable circumstance for a case (e.g. disability, minor, elderly).
    Selecting it on a case pulls in that circumstance's regulations on top of
    the category's base rules. Kept on the CASE, not stamped on the person."""

    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=120)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class ExportEvent(models.Model):
    """Append-only record of a document export — the deliberate, controlled
    leak. Exporting documents out of the system is high-sensitivity, so every
    export is logged: who did it, when, why, how many documents, what was in
    it (manifest snapshot), and the SHA-256 of the encrypted zip so a leaked
    archive can be traced back here. The zip password is NEVER stored — it is
    shown once to the exporter and not kept anywhere."""

    exported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="exports",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    document_count = models.PositiveIntegerField()
    reason = models.CharField(max_length=255)   # why / for whom (accountability)
    manifest = models.TextField()                # immutable snapshot of contents
    sha256 = models.CharField(max_length=64)     # of the encrypted zip

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.exported_by} ({self.document_count} docs)"


class DocumentAccessEvent(models.Model):
    """Append-only log of document OPEN attempts — allowed AND denied. Opening
    is the access checkpoint that navigation must pass (guardrail #3); every
    open, and every blocked attempt on a shielded person, is recorded with who,
    what, and the outcome. Snapshots the label so the trail survives deletion."""

    class Action(models.TextChoices):
        OPEN = "open", "Open / download"
        EXPORT = "export", "Export"

    opened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="document_opens",
    )
    document = models.ForeignKey(
        Document, on_delete=models.SET_NULL, null=True, blank=True, related_name="access_events",
    )
    document_label = models.CharField(max_length=120)          # snapshot
    person = models.ForeignKey(
        "people.Person", on_delete=models.SET_NULL, null=True, blank=True, related_name="access_events",
    )
    action = models.CharField(max_length=8, choices=Action.choices, default=Action.OPEN)
    allowed = models.BooleanField()
    reason = models.CharField(max_length=255, blank=True)      # export reason, or why denied
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        verb = "opened" if self.allowed else "DENIED"
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.opened_by} {verb} {self.document_label}"


class CaseHandoff(models.Model):
    """A requested move of a case to another department. Moving a case is gated:
    it must be approved by a department head (Lead) of the holding department,
    and approval is blocked until the case is complete (see
    cases.handoff.handoff_blockers) so the receiving department never inherits a
    case missing its required basis. On approval the case's owner_department
    moves and a StatusEvent records it."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name="handoffs")
    from_department = models.ForeignKey(
        Department, on_delete=models.PROTECT, related_name="+",
    )  # snapshot of the holder at request time
    to_department = models.ForeignKey(
        Department, on_delete=models.PROTECT, related_name="+",
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="handoffs_requested",
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    note = models.CharField(max_length=500, blank=True)        # why it's moving
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.PENDING)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        null=True, blank=True, related_name="handoffs_decided",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["-requested_at"]

    def __str__(self):
        return f"{self.case.ref}: {self.from_department} → {self.to_department} ({self.status})"


class DocumentType(models.Model):
    """Controlled vocabulary of document types (e.g. consent form, ID
    verification, assessment report). A CaseCategory can require certain types;
    a case isn't complete until a document of each required type is attached to
    it (see cases.handoff.handoff_blockers)."""

    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=120)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class DocumentActivity(models.Model):
    """Append-only trail of what happens to a document — added, edited,
    journalized, removed — captured with the acting user. So nothing enters or
    changes silently, and the record survives even if a draft is later deleted
    (the document FK goes null, the label snapshot stays)."""

    class Action(models.TextChoices):
        ADDED = "added", "Added"
        EDITED = "edited", "Edited"
        JOURNALIZED = "journalized", "Journalized"
        REMOVED = "removed", "Removed"

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="document_activities",
    )
    action = models.CharField(max_length=12, choices=Action.choices)
    document = models.ForeignKey(
        Document, on_delete=models.SET_NULL, null=True, blank=True, related_name="activities",
    )
    document_label = models.CharField(max_length=120)          # snapshot
    case = models.ForeignKey(
        Case, on_delete=models.SET_NULL, null=True, blank=True, related_name="document_activities",
    )
    person = models.ForeignKey(
        "people.Person", on_delete=models.SET_NULL, null=True, blank=True, related_name="document_activities",
    )
    detail = models.CharField(max_length=255, blank=True)      # e.g. journal number / what changed
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.actor} {self.action} {self.document_label}"
