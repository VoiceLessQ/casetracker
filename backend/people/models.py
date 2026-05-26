import uuid

from django.conf import settings
from django.db import models

from . import crypto
from .fields import EncryptedCharField, EncryptedTextField


class PersonQuerySet(models.QuerySet):
    def lookup(self, term):
        """Find people by name or CPR. Name match is case-insensitive partial.
        CPR is encrypted at rest, so it is matched EXACTLY via its blind index
        (dash-tolerant: '0101901234' finds '010190-1234'). Partial-CPR substring
        search is intentionally not possible — encryption is the trade.

        Name lookup is the sensitive path (enumeration/snooping) — log and
        access-limit it in a real deployment.
        """
        term = (term or "").strip()
        if not term:
            return self.none()
        q = models.Q(name__icontains=term)
        bidx = crypto.blind_index_for_term(term)
        if bidx:
            q |= models.Q(cpr_bidx=bidx)
        return self.filter(q)


class Person(models.Model):
    """A citizen record.

    PLACEHOLDER DATA ONLY — every field is synthetic. Never enter a real CPR
    number or real personal details. Use generated CPRs in a test range so a
    real one can never slip in.
    """

    cpr = EncryptedCharField(
        max_length=255, null=True, blank=True,
    )  # DDMMYY-XXXX (placeholder), ENCRYPTED AT REST. NULL while pre-CPR.
    cpr_bidx = models.CharField(
        max_length=64, null=True, blank=True, editable=False, db_index=True,
    )  # keyed HMAC of the normalised CPR — enables exact lookup + uniqueness
    uid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    # ^ permanent internal id. The folder is keyed on THIS, for life — never on
    #   the CPR — so the folder never moves when a CPR is assigned/corrected or a
    #   parent changes. Parent/guardian links live in the family tree, not here.
    name = models.CharField(max_length=160)                            # placeholder
    address = EncryptedCharField(max_length=600, blank=True)           # ENCRYPTED (shielding case)
    birth_date = models.DateField(null=True, blank=True)
    note = EncryptedTextField(blank=True)                              # ENCRYPTED free personal-info text
    is_shielded = models.BooleanField(
        default=False,
        help_text="Protected person (e.g. address protection / abuse case). "
                  "Their documents can only be OPENED by users with an explicit "
                  "access grant — navigation alone never suffices.",
    )

    objects = PersonQuerySet.as_manager()

    class Meta:
        ordering = ["name"]
        permissions = [
            ("search_all_persons", "Can search the whole citizen base (intake / borgerservice)"),
        ]
        constraints = [
            # CPR uniqueness moved off the (now non-deterministic) ciphertext
            # onto the blind index; only enforced for people who have a CPR.
            models.UniqueConstraint(
                fields=["cpr_bidx"],
                condition=models.Q(cpr_bidx__isnull=False),
                name="uniq_person_cpr_bidx",
            ),
        ]

    def _sync_cpr_bidx(self):
        self.cpr_bidx = crypto.cpr_blind_index(self.cpr) if self.cpr else None

    def clean(self):
        super().clean()
        self._sync_cpr_bidx()   # so the uniqueness constraint validates on the new CPR

    def save(self, *args, **kwargs):
        self._sync_cpr_bidx()   # authoritative: keep the index in step with the CPR
        super().save(*args, **kwargs)

    def __str__(self):
        label = self.cpr or f"no CPR · {self.uid.hex[:8]}"
        return f"{self.name} ({label})"

    @property
    def has_own_identity(self):
        """True once the person has their own CPR (no longer provisional)."""
        return bool(self.cpr)

    @property
    def folder_key(self):
        """Opaque, permanent folder key derived from the internal uid — NOT the
        CPR. Because it's keyed on a lifelong id, the folder never moves: a CPR
        being assigned, corrected, or a parent changing has no effect on where
        the documents live. The uid is random (not PII), so it can sit in the
        path directly — no secret pepper is needed now that the path no longer
        derives from the CPR."""
        return self.uid.hex

    @property
    def drive_folder(self):
        """{userpersonal}: one flat, independent folder per person, for life.
        Never nested under anyone — parents and guardians live in the family
        tree (Relationship), not the filesystem — so adoption or a parent's
        death never moves it and never grants/removes access by side effect."""
        from django.conf import settings
        root = getattr(settings, "MUNICIPAL_DRIVE_ROOT", "drive")
        return f"{root}/{self.folder_key}"

    # Query patterns (no helper, to keep direction unambiguous):
    #   this person's children:  p.relations.filter(relation=Relationship.Relation.CHILD)
    #   this person's parents:    p.relations.filter(relation=Relationship.Relation.PARENT)
    #   current ones only:        add .current()  (excludes ended edges)
    #   who lists this person:    p.related_to.all()


class RelationshipQuerySet(models.QuerySet):
    def current(self):
        """Only relationships still in force (not ended)."""
        return self.filter(ended_on__isnull=True)


class Relationship(models.Model):
    """One directed family edge. Reads as: `relative` is the <relation> of `person`.
    Example: person=Anna, relative=Ben, relation=CHILD  ->  Ben is Anna's child.

    Edges are NEVER overwritten or deleted. When a relationship ends — a parent
    dies, a child is adopted — set `ended_on` (+ reason) on the old edge and keep
    it as history, then add a new edge. The previous parent link is preserved
    alongside the new one; nothing is lost. `.current()` filters to live edges.

    Store edges from both sides if you want full traversal (Anna->CHILD->Ben
    and Ben->PARENT->Anna), or store one side and infer the inverse in queries.
    """

    class Relation(models.TextChoices):
        PARENT = "parent", "Parent"
        CHILD = "child", "Child"
        SPOUSE = "spouse", "Spouse / partner"
        GUARDIAN = "guardian", "Guardian"
        SIBLING = "sibling", "Sibling"

    class EndReason(models.TextChoices):
        DECEASED = "deceased", "Deceased"
        ADOPTION = "adoption", "Adoption / new guardianship"
        COURT = "court", "Court order"
        OTHER = "other", "Other"

    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="relations")
    relative = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="related_to")
    relation = models.CharField(max_length=16, choices=Relation.choices)
    started_on = models.DateField(null=True, blank=True)
    ended_on = models.DateField(null=True, blank=True)   # NULL = still current
    ended_reason = models.CharField(max_length=16, choices=EndReason.choices, blank=True)

    objects = RelationshipQuerySet.as_manager()

    class Meta:
        unique_together = ("person", "relative", "relation")

    @property
    def is_current(self):
        return self.ended_on is None

    def __str__(self):
        state = "" if self.ended_on is None else f" — ended {self.ended_on}"
        return f"{self.relative} is {self.get_relation_display().lower()} of {self.person}{state}"


class PersonNote(models.Model):
    """A dated, append-only note that travels WITH the person across all their
    cases — a running record, newest first.

    Visibility is explicit, NOT department-scoped by default:
      - ALL_STAFF means every caseworker/social worker can see it. This is the
        sensitive setting; in a real deployment, reads here should be access-
        logged, and the field exists so broad visibility is a deliberate choice.
      - DEPARTMENT keeps it to the author's department.
    Append-only is enforced in admin so the running record can't be rewritten.
    """

    class Visibility(models.TextChoices):
        ALL_STAFF = "all", "All caseworkers / social workers"
        DEPARTMENT = "dept", "Owning department only"

    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="notes")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    text = EncryptedTextField()                                        # ENCRYPTED at rest
    visibility = models.CharField(
        max_length=8, choices=Visibility.choices, default=Visibility.ALL_STAFF,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]   # newest first, so new vs. old is obvious

    def __str__(self):
        return f"{self.person} · {self.created_at:%Y-%m-%d}"


class PersonAccessGrant(models.Model):
    """An explicit permission for one user to OPEN a shielded person's documents.

    This is the heart of guardrail #3: navigation never implies access. Being
    able to reach a person (search, family tree, a link) does NOT let you open
    their documents — for a shielded person that requires one of these grants,
    checked at the open/export action and logged. Revoke by setting `expires_on`
    to a past date (kept as history, not deleted)."""

    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="access_grants")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="document_access_grants",
    )
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="access_grants_made",
    )
    reason = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_on = models.DateField(null=True, blank=True)   # NULL = no expiry

    class Meta:
        ordering = ["-created_at"]

    def is_active(self, on=None):
        from datetime import date
        on = on or date.today()
        return self.expires_on is None or self.expires_on >= on

    def __str__(self):
        return f"{self.user} → {self.person} (by {self.granted_by})"


class SearchEvent(models.Model):
    """Append-only log of person searches — search is access, so it's recorded.

    To avoid re-importing the CPR we worked to remove, the raw term is stored
    ONLY on a miss (no person to point at); on a hit we record the matched
    people (the uid we now know they looked at), not the term. Oversight-only:
    this log is itself a sensitive PII surface."""

    class Kind(models.TextChoices):
        NAME = "name", "Name"
        CPR = "cpr", "CPR"

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="person_searches",
    )
    kind = models.CharField(max_length=8, choices=Kind.choices, default=Kind.NAME)
    term = models.CharField(max_length=255, blank=True)        # stored only on a miss
    matched = models.ManyToManyField(Person, blank=True, related_name="search_hits")  # on a hit
    result_count = models.PositiveIntegerField(default=0)
    break_glass = models.BooleanField(default=False)           # reached outside own scope
    reason = models.CharField(max_length=255, blank=True)      # required for break-glass
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        what = self.term or f"{self.result_count} match(es)"
        glass = " [BREAK-GLASS]" if self.break_glass else ""
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.actor} searched {self.kind}: {what}{glass}"
