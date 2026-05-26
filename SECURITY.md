# Security model — CaseTracker

How this prototype protects data, what an operator must configure, and what is
deliberately **not** solved yet. Read this before deploying anywhere beyond a
local synthetic-data sandbox.

> **Prototype rule:** this system is for **synthetic / placeholder data only**.
> It must never hold a real CPR, real name/address, or real citizen record, and
> must never connect to a real CPR register, MitID, or system of record.

## Before any real data — governance, not just code

The controls below are necessary but **not sufficient** to hold real citizen
data. Custom-built cryptography and access control on regulated personal data
require, at minimum: an **independent security review** of the crypto and data
handling, a **named data controller** accountable for it, a documented
key-management procedure, and a lawful basis for processing. Good security code
raises the bar for who may operate the system — it does not remove the need for
institutional ownership and external audit. **Do not run this on real data on
the strength of this document, or one author's implementation, alone.**

## Design posture

- **Index, not vault.** The database stores *links* (`Document.location`) and
  metadata, never the document files themselves. The files live on the
  municipality's drive (`MUNICIPAL_DRIVE_ROOT`). A database breach therefore
  leaks metadata, not files.
- **Three axes kept separate:** family tree (navigation) / drive folder
  (storage, keyed on a permanent `uid`) / access (permission). Reaching a person
  never implies permission to open their documents.
- **Append-only history.** `CaseLog`, `PersonNote`, `StatusEvent`, `ExportEvent`,
  and `DocumentAccessEvent` are added, never edited or deleted.

## 1. Encryption at rest

Sensitive fields are encrypted in the database with authenticated encryption
(Fernet: AES-128-CBC + HMAC-SHA256). A stolen DB or backup shows only ciphertext
for these columns, because the key lives in configuration, not the data.

Encrypted fields:
- `people.Person.cpr` — plus `cpr_bidx`, a keyed-HMAC **blind index** so the CPR
  can still be looked up exactly (dash-tolerant) and kept unique, without
  decrypting. Partial-CPR substring search is intentionally not possible.
- `people.Person.address`
- `people.Person.note`
- `people.PersonNote.text`

Keys (see `people/crypto.py`) are derived via HKDF from a single master secret:
- `FIELD_ENCRYPTION_KEY` — master for field encryption and the blind index.
- `BACKUP_ENCRYPTION_KEY` — optional; if unset, the backup key derives from
  `FIELD_ENCRYPTION_KEY` (distinct HKDF context).

> **OPERATIONAL WARNING:** lose `FIELD_ENCRYPTION_KEY` and the encrypted fields
> are unrecoverable. In production set it from a secret manager / KMS, never
> commit it, and back it up **separately** from the database.

**What this does and does NOT protect.** Encryption at rest protects a *stolen
database file or backup*. It does **nothing** against a compromised application
server or an authorised/malicious insider — both see plaintext, because the app
must decrypt to function. So the real security posture is key management: where
the master key lives, who can reach it, and how it rotates — not the cipher.

**Blind-index caveat.** `cpr_bidx` is a *keyed* HMAC (not a plain hash), so it
reveals nothing on its own. But a CPR is low-entropy (well under ~30 bits of
real variation), so if the master key leaks an attacker can brute-force the
index offline and re-identify every CPR. Field encryption and the blind index
derive from the **same** master key — that one secret compromises both. This is
the same low-entropy trap as any hash over a CPR; the keyed HMAC only holds
while the key is secret.

## 2. Encrypted backups

- `python manage.py backup_db` — writes `backups/<timestamp>.sqlite3.enc`, an
  encrypted snapshot. The raw SQLite file is snapshotted (so field-level
  encryption stays intact inside) and then the whole file is encrypted: the
  backup key opens the file, the field key is still needed for the crown jewels.
- `python manage.py restore_db <file> --output <path>` — decrypts to an explicit
  path; refuses to overwrite without `--force`; fails cleanly on a wrong key.
- Backups are gitignored (`backups/`, `*.sqlite3.enc`).

**Also encrypt the live volume.** `backup_db` protects the backup artifact; run
the database on an encrypted disk (LUKS / cloud disk encryption) so the working
file and its journal/WAL are protected at rest too. For Postgres, pipe `pg_dump`
through the same backup key instead of the SQLite path.

## 3. Access control

### Department scoping
`cases.admin.ScopedAdmin` limits the rows a user sees to the departments they
belong to (`org.Membership`). Superusers see everything. Cases and everything
hanging off them (status events, documents, follow-ups, logs, assignments) are
scoped this way; direct-URL access to another department's row 404s.

### Roles (default access)
`Membership.role` is enforced per department:

| Role | View | Add/edit cases | Reassign / hand off | Delete |
|------|------|----------------|---------------------|--------|
| Viewer | yes | no | no | no |
| Member | yes | yes | no | no |
| Lead | yes | yes | yes | no |
| Superuser | yes | yes | yes | yes |

Deletes in the case area are superuser-only. A case can only be filed into a
department where the user is Member+.

**Provisioning:** `python manage.py setup_roles` creates the `Caseworker`
permission group (the default capability set). To onboard a user: tick *Staff
status*, add the `Caseworker` group, then set their department role(s) via the
membership inline on the user page.

### Document access gate (guardrail #3)
Opening a document is a checkpoint **separate from navigation**:
- `Person.is_shielded` marks a protected person (address protection / abuse).
- A shielded person's documents can be opened only by a user holding an active
  `PersonAccessGrant` (managed on the person page or its own admin; `granted_by`
  is stamped automatically; revoke by setting an expiry).
- Enforced by `people.access.can_open_person_documents` on the **Open /
  download** action and on **export** (shielded documents are excluded unless
  granted).
- Every open and every blocked attempt is recorded in `DocumentAccessEvent`
  (append-only).

For non-shielded persons, department scope remains the access boundary; opens
are still logged.

### Search (snooping is the dominant risk)
The recurring breach in systems like this is an authorised worker looking up
someone they have no business looking at. Search is access, so it is **scoped
and accountable**, not protected by the cipher:
- **Scoped** — `people.access.searchable_persons` limits a worker to people they
  have a reason to reach (subjects of their departments' cases); browsing the
  whole citizen base is closed. **Intake/borgerservice** is a role
  (`people.search_all_persons`, the `Borgerservice (intake)` group) with a
  legitimately broad scope, so onboarding isn't stranded.
- **Logged** — every explicit search writes an append-only `SearchEvent`. To
  avoid re-importing the CPR, a **hit logs the matched person (uid), not the
  term**; only a **miss stores the raw term**. The `SearchEvent` log is itself a
  sensitive PII surface and is **oversight-only (superuser)**.
- **Shielded persons are existence-hidden** from search/browse for anyone
  without a grant — a search won't even confirm the record exists.
- **Break-the-glass** (`/admin/people/person/break-glass/`) lets a caseworker
  reach outside their scope, but demands a stated reason and is logged loudly
  (`break_glass=True`) for review. It never reveals shielded-without-grant. It's
  the rare exception; routine broad search is the intake role, so the flag stays
  meaningful.

**Scope of the gate — read carefully; shielding here is PARTIAL.** The check is
on opening the document *content* (the Open/download action and export). It does
**not** hide a shielded person from navigation, and it does **not** cover:
- the person's own decrypted fields (address, notes) on the Person page — any
  in-scope staff who open the record see them;
- document-list metadata (that a document exists, its label) in the changelist;
- search results, family-tree traversal, and counts (existence leaks).

A complete shielding implementation must gate **every** read path, because a
gate on one path with leaks on the others looks safe but isn't. Today only
document *opening/export* is gated. The biggest open gap is the person record's
own decrypted fields, not the document view.

## 4. Exporting documents (the controlled leak)

`Export selected as encrypted zip` (Lead/superuser only):
- Packs selected documents into one **AES-256** encrypted zip (`pyzipper`).
- Generates a **unique one-time password per zip**, shown once, **never stored**.
- Requires a reason and is recorded in `ExportEvent` (who, when, count, reason,
  and the zip's SHA-256 for traceability).
- File reads are confined to the drive root (no path traversal via `location`).
- Shielded documents without a grant are excluded (and the attempt logged).

You cannot both hand a zip to an outside party and keep it "readable only here":
once it leaves it is readable wherever the recipient opens it. The control is
that the release is deliberate, minimal, logged, and the artifact itself is
encrypted.

## 5. Audit trails

Append-only, never editable in admin:
- `CaseLog`, `PersonNote` — narrative records.
- `StatusEvent` — status/department handoffs.
- `ExportEvent` — every export.
- `DocumentAccessEvent` — every document open and blocked attempt.

Records (journaling): a `Document` journalized onto a case gets a unique journal
number + date (`journalize`, format via `JOURNAL_NUMBER_FORMAT`). It is then
**immutable at the application layer** — the admin locks its content fields and
blocks deletion. This is append-only *enforcement*, NOT tamper-evidence: anyone
with direct database access can still alter rows. Real tamper-evidence (hash-
chaining, signed entries, or WORM storage) is **not** implemented. The per-case
journal is a read-only chronological view on the case page.

> All "append-only / never editable" claims in this document mean *enforced in
> the application/admin layer*. None of them are tamper-proof against direct
> database or filesystem access — that requires storage-level controls.

## Deployment checklist

- [ ] `SECRET_KEY` set from the environment (not the dev default).
- [ ] `DEBUG=0`.
- [ ] `ALLOWED_HOSTS` set to the real host(s).
- [ ] `FIELD_ENCRYPTION_KEY` (and ideally `BACKUP_ENCRYPTION_KEY`) from a secret
      manager; backed up separately from the database.
- [ ] Database on an encrypted volume; `backup_db` scheduled; restores tested.
- [ ] HTTPS only; secure/HTTPOnly cookies; HSTS.
- [ ] `python manage.py setup_roles`; users provisioned with the `Caseworker`
      group + department roles; superuser accounts minimised.
- [ ] Shielded persons flagged; access grants reviewed.

## Known limitations / not yet built

Be honest about these — do not treat the prototype as production-hardened:

- **Shielding now covers documents AND search** (existence-hidden in
  search/browse), but still not the person's own decrypted fields on the Person
  change page if a worker reaches it in scope, nor document-list metadata. Treat
  shielding as strong-but-not-total. Non-shielded citizens rely on department
  scope, not per-citizen grants; opens and searches are logged.
- **Key management is the whole posture, and it's external to this code.** One
  master key protects field confidentiality *and* the CPR blind index; its
  storage/rotation/access is the real control and is not solved here.
- **Impersonation** (`testing` app) is superuser-only but **not audited** — a
  real deployment must log every impersonation and every action taken while
  impersonating, and restrict it far more tightly. Consider removing the
  `testing` app entirely in production.
- **`PersonNote.visibility`** (all-staff vs department) is recorded but **not
  enforced on reads**.
- **`StatusEvent`** is not auto-created on status/department change (manual).
- **Name and `birth_date`** are not encrypted (name must stay searchable;
  encrypting it would disable partial-name lookup).
- Search is now scoped + logged + break-the-glass, but there's **no rate-limiting
  and no automated review** of the search log yet — the oversight value depends
  on someone actually reading it (start with the break-glass filter). A
  "searches not followed by a case action" report is the high-signal next step.
- **No tests, no non-admin frontend, no real drive integration** yet.
