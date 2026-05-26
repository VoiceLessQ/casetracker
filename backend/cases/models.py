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
    # Email metadata (kind=EMAIL): kept so correspondence reads as a timeline.
    email_from = models.CharField(max_length=255, blank=True)
    email_subject = models.CharField(max_length=255, blank=True)
    email_sent_at = models.DateTimeField(null=True, blank=True)
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-added_at"]
        constraints = [
            models.CheckConstraint(
                name="document_has_case_or_person",
                check=models.Q(case__isnull=False) | models.Q(person__isnull=False),
            ),
        ]

    def __str__(self):
        return f"{self.label} → {self.location}"


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


class CaseCategory(models.Model):
    """Controlled case type. Free text can't reliably drive the regulation map;
    a fixed vocabulary can. Each category carries the regulations that apply to
    it (via RegulationRule)."""

    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=120)
    active = models.BooleanField(default=True)

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
