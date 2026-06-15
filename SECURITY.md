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

## Threat model — what a compromised network actually gets

If an attacker reaches the local network (a server, a share, a backup), what can
they actually read? Stated honestly, because each line is easy to over-hear as
more reassuring than it is:

- **Stolen files → ciphertext.** A copied database or backup yields ciphertext
  for the encrypted fields (CPR, address, notes), and the documents aren't here
  at all (index, not vault). Useless without the key. This is the real win.
- **But "only metadata" is not "fine".** What stays in plaintext — names, the
  fact that a person *has* a case in a given department, the category, mail-trail
  subjects, journal entries — is sensitive PII on its own. "X has a
  child-protection case in Social" is a serious disclosure with no document
  attached. A metadata-only breach is a *smaller* incident, not a non-incident.
- **A stolen account is the dangerous case, and crypto does not help there.** A
  logged-in session decrypts for whoever holds it, so encryption-at-rest gives
  nothing against a hijacked account. What limits the damage is **department
  scoping** (they see one department, not the whole base — the real control
  here), **logging** (every open/search/export is attributable), and **how fast
  the account can be disabled**. On-screen PII masking is a speed-bump with an
  audit trail, not a barrier — a logged-in attacker can usually trigger the
  reveal; it just gets recorded.

### Key isolation is a HARD REQUIREMENT, not a feature

Everything above depends on one thing the **code cannot enforce**: the
encryption key must live somewhere the network attacker can't reach — a secret
manager / KMS / a mounted secret on separate infrastructure — **never on the
same host as the database**. The code only makes isolation *possible*
(`FIELD_ENCRYPTION_KEY_FILE`, a pluggable KMS provider); the **deployer** makes
it *true*.

> **The encryption model is void the moment the key sits beside the data.**
> Treat key isolation as a precondition for handling real data, not a
> nice-to-have — it is the single control the application cannot guarantee for
> you, and the blind index (low-entropy CPR) is brute-forceable the instant the
> key leaks.

## Security is only as good as the people — including whoever built it

Every control in this document shapes behaviour and records it; none of it makes
a person careful. The real failures in systems like this are human, not
cryptographic: a shared login, the key left on the same box, a laptop left
unlocked, a careless export, an account that should have been disabled months
ago. The technical layer can make the right thing easy and the wrong thing
logged — it cannot make the right thing happen.

That includes the author and the operator. Being the person who built it is not
an exemption — not for VoiceLessQ, not for anyone. A maintainer holding the keys
is as much a potential point of failure as any caseworker, and should be held to
the same scope, logging, and least-privilege as everyone else — ideally so the
system does not *let* any single person, author included, quietly become the
weak point. Security here is a discipline everyone who touches it keeps up, not
a feature that is ever "done".

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

**Bring your own key / encryption (the base is pluggable).** The built-in
Fernet+HKDF is the default, but it isn't hardcoded:
- **Key from your own location** — set `FIELD_ENCRYPTION_KEY_FILE` (or
  `BACKUP_ENCRYPTION_KEY_FILE`) to a path; the key is read from that file (a
  mounted secret, or a file on your own network) instead of an env var. The key
  never has to live on the app host.
- **Your own provider** — set `FIELD_ENCRYPTION_BACKEND` to a dotted path of a
  `people.crypto.CryptoProvider` subclass (your KMS/HSM, a different cipher,
  envelope encryption). The app calls the same functions; only the backend
  changes. Empty = the built-in base.
- **Caveats:** switching key or provider does **not** re-encrypt existing data —
  that needs a decrypt-old / encrypt-new migration; the blind index must use a
  key your provider can reproduce, or CPR search breaks; and prefer a reputable
  KMS/HSM over a homegrown cipher.

> **OPERATIONAL WARNING:** lose `FIELD_ENCRYPTION_KEY` and the encrypted fields
> are unrecoverable. In production set it from a secret manager / KMS / mounted
> file, never commit it, and back it up **separately** from the database.

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
- **Redundancy (3-2-1):** `backup_db --also-copy-to <dir>` (repeatable) drops the
  encrypted file in extra locations in one run. Point it at a *different volume /
  mounted offsite share* — a second folder on the **same disk is not real
  redundancy** (one disk failure or ransomware run takes both). The `.enc` is
  ciphertext, so copying it to less-trusted/offsite storage is safe. Keep
  credentialed remote uploads (S3/Azure Blob) in the scheduler/ops layer, not in
  the app, so backup destinations don't add attack surface.

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

**Scope of shielding (don't over-read it).** Shielding gates *opening documents*
and *search/browse visibility*. It does **not** hide a shielded person's own
(decrypted) record fields from a worker who legitimately reaches them in scope —
e.g. via a shared case. That's a deliberate, documented threat-model call
(in-scope means a case-based reason to reach the person), not total invisibility.

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

### Reducing what's exfiltratable / on screen (insider focus)
The likelier threat is an authorised insider, and logging only catches them
after the fact — so the channel is squeezed and the screen surface reduced:
- **PII masked by default.** CPR shows masked (`••••••-1234`) in lists, the
  worklist, and every place a person is referenced (`Person.__str__`). The full
  value appears only on the scoped detail page — cutting casual harvesting and
  screenshot exposure.
- **Export is capped.** `EXPORT_MAX_DOCUMENTS` (default 50) blocks "select-all →
  export"; larger pulls must be deliberately narrowed.
- **Exports are watermarked.** Each zip's `MANIFEST.txt` records who pulled it
  (username + user id), when, and a unique export id, and the filename carries
  the same — so a leaked file traces straight back to the requester. That
  traceability is a deterrent, not just an audit line.

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

Append-only, never edited or deleted:
- `CaseLog`, `PersonNote`, `StatusEvent` — narrative records and status/department
  handoffs. Enforced at the **model layer**: the `AppendOnly` mixin refuses ORM
  updates and deletes on the model itself (not only in admin), and their
  case/person parents are `PROTECT`ed so a case or person deletion can't cascade
  the trail away.
- `ExportEvent` — every export. (admin-layer)
- `DocumentAccessEvent` — every document open and blocked attempt. (admin-layer)
- `DocumentActivity` — every document add / edit / journalize / remove, with the
  acting user. Snapshots the label, so the trail survives even if a draft is
  deleted — nothing enters or changes silently. Surfaced in the case journal.

Records (journaling): a `Document` journalized onto a case gets a unique journal
number + date (`journalize`, format via `JOURNAL_NUMBER_FORMAT`). It is then
**immutable at the application layer** — the admin locks its content fields and
blocks deletion. This is append-only *enforcement*, NOT tamper-evidence: anyone
with direct database access can still alter rows. Real tamper-evidence (hash-
chaining, signed entries, or WORM storage) is **not** implemented. The per-case
journal is a read-only chronological view on the case page.

> Append-only for `CaseLog` / `PersonNote` / `StatusEvent` is enforced at the
> model layer (ORM updates and deletes refused); the other event logs are
> enforced in the admin layer. Either way it is **not tamper-proof**: a bulk
> `QuerySet.delete()` and direct database or filesystem access still bypass it —
> real tamper-evidence requires storage-level controls (hash-chaining, signed
> entries, WORM, or an external write-once log).

## Deployment checklist

- [ ] `SECRET_KEY` and `FIELD_ENCRYPTION_KEY` set from the environment (not the
      dev defaults). With `DEBUG` off the app **fails closed** — it refuses to
      start while either is still the dev placeholder — so this is enforced, but
      verify it.
- [ ] `DEBUG=0` (the default; `DEBUG=1` is local dev only).
- [ ] `ALLOWED_HOSTS` set to the real host(s).
- [ ] **Encryption key isolated from the database host** — from a secret manager
      / KMS / mounted secret, never on the same box as the DB (the model is void
      otherwise). Backed up separately. `BACKUP_ENCRYPTION_KEY` likewise.
- [ ] Database on an encrypted volume; `backup_db` scheduled; restores tested.
- [ ] HTTPS only; secure/HTTPOnly cookies; HSTS.
- [ ] `python manage.py setup_roles`; users provisioned with the `Caseworker`
      group + department roles; superuser accounts minimised.
- [ ] Shielded persons flagged; access grants reviewed.

## Hardening designed but deferred (needs ops or real usage)

These are the right next controls; they're parked deliberately, not forgotten —
some are deployment/ops (not app code), and detection/alerting has nothing to
detect on a prototype with no users. Build them when there's a real operator and
real traffic behind them:

- **Hold less data** — the biggest lever, and free: the M365 offload + not
  storing CPR, storing only fields actually used, and a **retention/purge**
  policy so old case data isn't liability sitting in a future breach.
- **Keys in Azure Key Vault**, isolated from the data — a key next to the
  database makes encryption-at-rest pointless. (Today: from a secret manager
  via `FIELD_ENCRYPTION_KEY`; Key Vault is the deployment target.)
- **MFA at the identity provider (Entra)** — kills most account-takeover; the
  single biggest win against the external attacker. Comes with the SSO work.
- **Tamper-evident audit** — ship `*Event` logs to an external write-once store
  / the municipality's SIEM, so the trail survives even if the app (or an
  insider with DB access) is compromised. App-layer append-only is not
  tamper-proof.
- **Least-privilege over time** — access expires when a case closes and drops on
  role change, instead of standing access accumulating.
- **Detection/alerting** — unusually large exports, searches not followed by a
  case action. Needs real usage to be meaningful (otherwise premature).
- **Platform hygiene** — admin IP-restricted, DB not publicly reachable, CSP,
  dependency scanning/patching, tested ransomware restore.

The meta, honestly: most real protection here is **holding less** and
**operational discipline** (access reviews, prompt offboarding, no shared
accounts, device security, a named data controller) plus the independent review
before real data — not more features. The biggest leaks in systems like this are
process failures, not missing code.

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
- **`StatusEvent`** is auto-created on the admin save and the handoff-approval
  path, but not at the ORM/signal layer — a direct `Case.save()` or a management
  command skips it.
- **Name and `birth_date`** are not encrypted (name must stay searchable;
  encrypting it would disable partial-name lookup).
- Search is now scoped + logged + break-the-glass, but there's **no rate-limiting
  and no automated review** of the search log yet — the oversight value depends
  on someone actually reading it (start with the break-glass filter). A
  "searches not followed by a case action" report is the high-signal next step.
- **No tests, no non-admin frontend, no real drive integration** yet.
