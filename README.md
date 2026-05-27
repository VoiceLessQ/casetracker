# CaseTracker (prototype)

A secure overlay for tracking **cases and the documents tied to them** — where a
case stands, who's working it, and what's outstanding. Its job is to **keep
things from slipping**: it surfaces cases that have gone unexplainedly quiet
("cold") and keeps a clear, append-only record of who did what, when. Built
around **strict access control, shielding of sensitive records, and a full audit
trail**. It is an **index, not a vault**: it links to documents on an existing
drive rather than storing them.

Suited to **regulated, document-heavy casework** — anywhere who-can-see-what and
a defensible record matter (public administration, legal, social services,
healthcare, HR, compliance).

**Placeholder data only.** Never enter real personal data (real IDs, names,
addresses, or records). It is shaped like a real system but must only ever hold
synthetic data, and must never connect to a real identity register or system of
record.

See **[SECURITY.md](SECURITY.md)** for the security model + deployment checklist,
and **[ARCHITECTURE.md](ARCHITECTURE.md)** for where it's headed — a thin
dashboard/index overlay over the organisation's Microsoft 365 drive and Outlook,
with identity and roles from Entra.

## What it does (built)

**Access & identity**
- Department scoping — a worker sees only their department's cases and what hangs
  off them; the person record is the shared spine, the case material is scoped.
- Roles per department (viewer / member / lead) enforced; provisioning via
  `setup_roles` + groups; an intake role for broad person search.
- Person search is **scoped, logged (append-only), shielding-aware**, with a
  reason-required **break-the-glass** path for out-of-scope lookups.

**Records & workflow**
- **Worker dashboard** (`/dashboard/`): my cases, my tasks, needs-attention
  (unexplained-stale), department queue — read-only, scope-aware.
- **Journaling** — documents carry a direction and get a per-case journal number;
  immutable once journalized. Per-case journal/activity timeline.
- **Handoffs** between departments are gated by **department-head approval** plus
  a completeness check (category, person, required legal refs, required document
  types).
- **Append-only trails**: status/department handoffs, narrative logs, document
  opens, exports, and document add/edit/journalize/remove.

**Data protection**
- **Field encryption at rest** (CPR + searchable blind index, address, notes) —
  pluggable (bring-your-own key/provider; Fernet+HKDF is the default base).
- **PII masked by default** on screen (full CPR only on the scoped detail page).
- **Document access gate** for shielded persons (explicit, expiring grants).
- **Export**: gated, logged, AES-256 password-protected, size-capped, watermarked.
- **Encrypted DB backup/restore** with optional offsite copy.

## Run it

```bash
cd backend
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py setup_roles          # create the Caseworker + Borgerservice groups
python manage.py createsuperuser
python manage.py runserver
```

Admin at http://127.0.0.1:8000/admin/.

### Demo it

```bash
python manage.py seed_demo            # synthetic departments, workers, people, cases
```

Then open **http://127.0.0.1:8000/dashboard/**. Log in as a seeded worker
(`anna@demo`, `bo@demo`, `david@demo`, `clara@demo`; password `demo12345`) or
impersonate them from the admin. Accounts are keyed on email with real names —
modelling how production provisions from SSO. The point of the demo:
the same dashboard shows *different* things per worker, and a shielded person
disappears entirely from search for a worker without a grant — the access model
made visible, not just described.

## Configuration (environment)

- `MUNICIPAL_DRIVE_ROOT` — where document links point (default `backend/drive`).
- `SECRET_KEY`, `DEBUG=0`, `ALLOWED_HOSTS` — set before exposing anywhere.
- `FIELD_ENCRYPTION_KEY` (or `FIELD_ENCRYPTION_KEY_FILE`) — at-rest field
  encryption; losing it makes those fields unrecoverable. `BACKUP_ENCRYPTION_KEY`
  (or `_FILE`) and `FIELD_ENCRYPTION_BACKEND` (bring-your-own crypto) — see
  SECURITY.md.
- `EXPORT_MAX_DOCUMENTS` (export cap), `JOURNAL_NUMBER_FORMAT` (journal numbering).

## Languages

The system is **translatable into any language**, not hardwired to one. The
worker dashboard is wrapped for translation and **switchable at runtime**
(`LocaleMiddleware` + a switcher posting to `i18n/setlang/`; default via
`LANGUAGE_CODE`). A **draft Danish** catalog ships — UI labels only, **needs a
native speaker's review** before real use. English is the source language;
Kalaallisut is configured but not yet translated.

Add or update a language:
```bash
python manage.py makemessages -l <code>     # extract strings → locale/<code>/LC_MESSAGES/django.po
# ...translate the .po (ideally a human translator)...
python manage.py compilemessages             # build .mo (needs the gettext toolchain)
```

Still to do: wrap the **model/admin labels** (the admin still shows our English
field names) — a mechanical pass — and a native review of the Danish.

- `org` — departments and memberships (the scoping unit + per-department roles).
- `people` — the people on cases, temporal family tree, dash-tolerant ID/name
  lookup, shielding + access grants, append-only notes, search logging.
- `cases` — cases, status history, document links + journaling, follow-ups,
  narrative logs, assignments, calendar, legal references, the
  category/circumstance → regulation map, handoff approval, document types, and
  the append-only audit-event models.
- `testing` — the impersonation tool below.

## Impersonation (test what a worker sees)

The point of the scoping is that a caseworker sees only their department's
material. As a superuser:

1. Provision a worker: admin → Users → Add; tick **is_staff**, add the
   **Caseworker** group, and set their department(s)/role via the membership
   inline. (Or just use the `seed_demo` workers.)
2. admin → Users → tick the worker → action **"Impersonate for testing"**.
3. You now see the admin (and `/dashboard/`) exactly as they do.
4. Return to yourself: visit **`/stop-impersonation/`**.

This is a **testing tool**. Real impersonation is a high-trust, audited
capability; restrict and log it far more tightly before any real use.

## Not built yet

- **Microsoft 365 / Graph track**: SSO/Entra federation + MFA, multi-tenant,
  SharePoint/OneDrive document storage, mail capture, Outlook calendar.
- **Native dashboard actions** (accept case / add follow-up) — today they link
  into the admin.
- **Detection/alerting** (large exports, searches with no follow-up),
  retention/purge, tamper-evident audit shipped to an external/SIEM store.
- Tests, a non-admin frontend, and real drive integration.

See ARCHITECTURE.md for the target and SECURITY.md's "deferred hardening" list.

## License

Apache-2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE). You may use, modify,
and redistribute it, but must retain the copyright and attribution notices.
Copyright 2026 VoiceLessQ.
