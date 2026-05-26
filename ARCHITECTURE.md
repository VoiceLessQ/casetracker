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

## Drive isolation & who owns permissions

- **Dedicated casework drive.** Content lives in its own SharePoint site /
  library, permissioned by Entra groups per department — not scattered across
  workers' personal OneDrives. Department scope is then inherited from the
  routine site/group permissions IT already operates; the blast radius is
  contained to that site.
- **The overlay uses delegated Graph permissions, never application
  permissions.** The app acts *as the signed-in worker* (their SSO session), so
  it is structurally incapable of seeing more than that worker already can, and
  there is no standing, app-owned, tenant-wide access key to steal. A compromise
  of the overlay yields nothing beyond the current user's own access. Trade-off:
  no broad background jobs; anything needing elevation (creating folders,
  setting ACLs) is done by IT or a separate narrow, audited path — not baked
  into the app.
- **Storage follows IT's structure; the shared spine is logical.** Physical
  documents sit department-organized inside the governed drive. "Person as
  shared spine" is a *logical* construct in the index (uid-keyed) — the overlay
  draws the thread across a person's cases. Do NOT create uid-keyed flat folders
  outside IT's site/permission structure; that builds a parallel permissioning
  surface IT must manage separately and undercuts the "we just use the drive"
  win.
- **IT owns the ACLs; M365 is the source of truth.** Per-person folders and
  shielding exceptions are a deliberate, owned process living in M365, with the
  overlay only reflecting them. Department access is additive (group-based,
  routine); **shielding is subtractive** (in the group, but blocked from this
  one person) — per-item unique permissions / broken inheritance, the messy
  exception, not routine IT setup. That is the owned 10% where mistakes are
  worst.

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

## Security is shared with the operators

The technical controls in this system are necessary but they are not where most
of the real-world risk lives. The majority of it lives with the people operating
it: IT maintaining the drive permissions and the shielding exceptions, the named
data controller's accountability, and workers handling exports, links, and what
they enter responsibly. No amount of code substitutes for that operational
discipline.

So the system's job is bounded and honest: make the right thing easy and the
wrong thing hard-and-logged. Concretely that is what the gated/logged export,
the append-only access log, the immutable journal, department scoping, and the
shielding gate are for — they shape and record behaviour, they do not replace
the humans and the institutional ownership around them. Design every new feature
to keep that split clear: the system enforces and records; the operators and IT
remain responsible for the access decisions and the data itself.

## Decisions leaning in, and still open

Settled (see sections above): overlay-not-store; identity + roles from Entra;
dedicated casework drive with delegated (not application) Graph permissions;
shared spine is logical while storage follows IT's structure; IT owns the ACLs.

Leaning / still to confirm:
- **Narrative text (notes, logs, journaling notes):** lean toward keeping it OUT
  of the index where practical — case notes are usually the most sensitive free
  text in the building, not a small surface. Tension: notes on the drive are
  clunky to search/render in a one-pane dashboard. This is the real trade.
- **CPR:** lean toward NOT storing it at all — reference the citizen by `uid`
  (folder is already uid-keyed). That removes the blind-index brute-force risk
  and most of the field-encryption surface. Cost: "find by CPR" becomes a
  Graph/source lookup mapping to uid, not a local query.
- **Index hosting:** the municipality's Azure tenant, not the developer's infra
  — inseparable from "the municipality is the data controller."
- **"Not see" model:** existence-hidden by default for shielding (knowing a
  person *has* a shielded case is itself the leak); visible-but-locked only as
  an explicit, logged exception for genuine cross-department coordination.
- **Metadata is not the safe part.** A mail trail's from/subject/date (and a
  journal registration's subject/direction) can be as revealing as a body —
  "from: psykiatrisk klinik". Scope and shield the metadata exactly like
  content; "we only kept metadata" is not a safety argument.
