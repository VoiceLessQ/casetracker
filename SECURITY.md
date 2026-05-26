# Security model — CaseTracker

How this prototype protects data, what an operator must configure, and what is
deliberately **not** solved yet. Read this before deploying anywhere beyond a
local synthetic-data sandbox.

> **Prototype rule:** this system is for **synthetic / placeholder data only**.
> It must never hold a real CPR, real name/address, or real citizen record, and
> must never connect to a real CPR register, MitID, or system of record.

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

- **Access gate covers the shielding case only.** Non-shielded citizens rely on
  department scope, not per-citizen grants. Opens are logged, not pre-authorised.
- **Impersonation** (`testing` app) is superuser-only but **not audited** — a
  real deployment must log every impersonation and every action taken while
  impersonating, and restrict it far more tightly. Consider removing the
  `testing` app entirely in production.
- **`PersonNote.visibility`** (all-staff vs department) is recorded but **not
  enforced on reads**.
- **`StatusEvent`** is not auto-created on status/department change (manual).
- **Name and `birth_date`** are not encrypted (name must stay searchable;
  encrypting it would disable partial-name lookup).
- **No rate-limiting / enumeration protection** on the CPR/name lookup.
- **No tests, no non-admin frontend, no real drive integration** yet.
