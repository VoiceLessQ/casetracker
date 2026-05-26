# CaseTracker (prototype)

A municipal case-status overlay: it tracks **where a case stands, what's
waiting, who's on it, and which regulations apply** — and links to documents
that live on the municipality's own drive. It is an index, not a vault.

**Placeholder data only.** Never enter a real CPR or real personal details.
This prototype is shaped like a real system but must only ever hold synthetic
data. It must never connect to a real CPR register or real records.

See [SECURITY.md](SECURITY.md) for the security model (encryption at rest,
access control, the document access gate, encrypted backups) and a deployment
checklist, and [ARCHITECTURE.md](ARCHITECTURE.md) for where this is headed — a
thin dashboard/index overlay over the municipality's Microsoft 365 drive and
Outlook, with identity and roles from Entra.

## Run it

```bash
cd backend
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Open http://127.0.0.1:8000/admin/ and log in.

Optional environment:
- `MUNICIPAL_DRIVE_ROOT` — where document links point (defaults to `backend/drive`).
- `SECRET_KEY`, `DEBUG=0`, `ALLOWED_HOSTS` — set before exposing anywhere.
- `FIELD_ENCRYPTION_KEY` — encrypts sensitive fields at rest (CPR, notes,
  address). Set from a secret manager; losing it makes those fields
  unrecoverable. `BACKUP_ENCRYPTION_KEY` — optional, for `manage.py backup_db`.

## Apps

- `org` — departments and who belongs to them (the scoping unit).
- `people` — citizens, family tree (temporal), CPR/name lookup, per-person
  flat folders keyed on a permanent id, running notes.
- `cases` — cases, status history, document links, follow-ups, narrative logs,
  assignments, calendar, legal references, and the category/circumstance →
  regulation map.
- `testing` — the impersonation tool below.

## Testing what a worker can see and do (impersonation)

The point of the scoping is that a caseworker only sees their department's
cases. To test that as a superuser:

1. Create a worker user: admin → Users → Add. Set **is_staff = True** (required
   to open the admin) and give them limited permissions if you want to test
   "can do" too.
2. Give them a department: admin → Memberships → Add (user + department).
3. admin → Users → tick the worker → action **"Impersonate for testing
   (view as this user)"**.
4. You now see the admin exactly as they do — department-scoped cases,
   append-only logs, owner-only calendar, the lot.
5. Return to yourself: visit **`/stop-impersonation/`**.

Notes:
- Only a superuser can start impersonation.
- A non-staff worker can't open the admin, so impersonating them shows nothing
  — set is_staff to test. You can always get back via `/stop-impersonation/`.
- This is a **testing tool**. Real impersonation is a high-trust, heavily
  audited capability; restrict and log it far more tightly before any real use.

## Not built yet (runtime, on top of this schema)

- Stale-case dashboard view (the query `Case.objects.stale()` exists).
- Materializing circumstance rules into `CaseLegalRef` rows on tick
  (`Case.applicable_rules()` computes them).
- Select + zip export of a person's document links (high-sensitivity; log it).
- Enforcing `PersonNote.visibility` and required-legal-ref on close.
