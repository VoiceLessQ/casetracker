# Target architecture — CaseTracker

Where this system is headed, and why. Written so the direction survives between
sessions. The code on `main` today is a Django-admin prototype on SQLite with
synthetic data; this document is the **target** it should grow toward, not a
description of what's fully built. See `SECURITY.md` for the security model and
`README.md` for how to run the current prototype.

## The core idea: overlay, not store

CaseTracker is a **system of engagement** — a worker-facing dashboard, an index,
and a journaling/audit layer — sitting on top of the municipality's existing
**system of record**, their Microsoft 365 drive (SharePoint / OneDrive) and
Outlook. The point is to make a caseworker's day easier (one pane: where a case
stands, who's on it, the people involved, the mail trail, the journal) **without
becoming the home of the actual data**.

> **Person = shared spine. Case material = department-scoped + role-gated.
> Shielded documents = grant-gated. The drive enforces the actual open.**

The heavy, regulated content stays where it is already governed. That keeps this
system thin, and it keeps the bulk of the compliance/encryption burden on
Microsoft's already-audited platform rather than on this codebase.

## The data split — "our data" vs offloaded

**Offloaded to the drive (never stored in this system):**
- Document files, email bodies, attachments, anything large.
- Calendars (Outlook).

**"Our data" (the index — what this system actually holds):**
- Case state: status, waiting-on, owner department, who's assigned, the
  category/circumstance → regulation map.
- A **link + lightweight metadata** for each document/email on the drive. For a
  mail trail that's from / subject / date / direction — enough to render the
  thread and click through, never the body.
- Journaling records: journal number / date / direction, pointing at the drive
  item (the registration, not the content).
- The family graph (temporal relationships) and case workflow notes/logs.

> **Mail trails are metadata, not content.** The trap to avoid: "have the emails
> as data" by importing bodies + attachments into the database. That re-absorbs
> the sensitive payload and the governance burden we are deliberately offloading.
> Store the pointer and the trail metadata; the `.eml`/`.msg` lives in the
> person's drive folder.

**Still under discussion:** whether narrative text (case notes, log entries,
journaling notes) stays in the index (searchable, append-only — field encryption
then earns its keep on a small surface) or is also offloaded to the drive. This
decides how much PII the database holds at all.

## Identity & authorization

**Identity comes from Microsoft (Entra ID / Azure AD).** Workers sign in with
their existing municipal account (SSO); this system does not create or store
passwords.

**Roles come from Entra groups.** An AD group (e.g. `Social-Caseworkers`,
`Social-Leads`) maps to a department + role in this system. IT manages group
membership in one place; this system maps groups to scope/role. This replaces
the manual user + `Membership` provisioning the prototype uses today — same role
shape, different source.

**See / not see — by department. Can do — by role.**
- **See:** you see your department's cases and everything hanging off them
  (documents, mail trail, journal, logs); not other departments' cases.
- **Do:** Viewer = read-only, Member = edit, Lead = reassign/hand off + export.
  Deletes stay locked; journalized records are immutable (app-layer).
- **Person is the shared reference**, not owned by a department: a citizen can
  have a Social case *and* a Tax case. Department scoping applies to the **case
  material**, not the person's existence — a Social worker sees that person's
  Social case and documents, not their Tax case.
- **Shielding** further restricts *opening* a specific person's documents to
  users with an explicit grant (`PersonAccessGrant`).

**Two authorization layers, kept in agreement:**
1. The overlay's roles decide what the dashboard *shows and lets you do*.
2. The drive's own permissions decide whether the actual file *opens*.

The clean rule: derive the overlay's roles from the same Entra groups, and let
the drive (SharePoint/OneDrive) be the final gate on content. **Clicking to open
hits the drive, which enforces that worker's permission** — so access is
enforced by the platform that owns the file, and every open is logged here.

## Documents, mail, calendars (Microsoft Graph)

- **Documents:** `Document.location` is a link to the file on the drive
  (SharePoint/OneDrive URL via Graph). Open/upload-through go through Graph.
- **Mail:** captured emails are written into the person's drive folder and
  linked here as `kind=email` with from/subject/date/direction. The dashboard
  renders the trail from that metadata; opening fetches the actual message.
- **Calendars:** integrate with Outlook via Graph rather than storing events
  here (the current `CalendarEvent` model becomes a reference/sync target).

## What exists today vs. the target build

**Built (on `main`, prototype):** department scoping, viewer/member/lead roles,
gated + logged + AES-256 password-encrypted export, field encryption at rest
(CPR + blind index, address, notes), encrypted DB backup/restore, the shielded
document access gate + access log, formal journaling (direction + per-case
journal numbers + app-layer immutability), `SECURITY.md`.

**The substantial future build:**
1. **A worker dashboard** — today the system is Django-admin only; the
   worker-facing dashboard is a frontend that does not exist yet.
2. **Microsoft Graph integration** — SSO/Entra identity + group→role mapping,
   SharePoint/OneDrive documents, mail capture, Outlook calendar. This is the
   bulk of the engineering and re-raises "who maintains the integration."

The index/journaling backbone is mostly in place; the dashboard and the Graph
integration are the lift.

## Governance gate — before any real data

The controls above are necessary but **not sufficient** to hold real citizen
data. Custom-built crypto and access control on regulated personal data need an
**independent security review**, a **named data controller**, a documented
key-management procedure, and a lawful basis for processing. Moving the heavy
data into M365 shrinks this system's surface (a real benefit), but it does not
remove the institutional requirement. **Do not run this on real data on the
strength of this design alone.**

## Open decisions

- Does narrative text (notes, logs, journaling notes) stay in the index or move
  to the drive? (Determines remaining PII footprint.)
- Can the citizen be referenced by their permanent `uid` + drive folder so the
  CPR need not be stored here at all? (The folder is already `uid`-keyed.)
- Where is the slimmed-down index database hosted (their Azure tenant)?
- "Not see" model per data type: fully filtered (existence hidden) vs.
  visible-but-locked (cross-department coordination). Default: department scope
  hides other departments' case material; shielding hides shielded documents.
