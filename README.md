# Employee Onboarding Automation

Automates routine employee onboarding: verifies required setup steps and routes
nonstandard or ambiguous cases to IT for manual review instead of guessing.
Full business rules live in [`REQUIREMENTS.md`](REQUIREMENTS.md); this README
summarizes what's actually built.

## Status

| Phase(s) | Covers | Status |
|---|---|---|
| 1–2 | Load/validate the source files, business decisions (scope, scheduling, group mapping, card validation, duplicates) | Done |
| 3–5 | Entra account, Microsoft 365 E3 licensing, standard group assignment — each against a simulated service | Done |
| 6 | Combines the three steps + the corporate-card decision into one per-request workflow | Done |
| 7 | Audit evidence — one JSONL record per attempt, append-only | Done |
| 8 | Notifications — manager completion email / IT alert (simulated) | Done |
| 9–11 | Rerun detection, one top-level entry point, batch processing | Done |
| 12 | Real Microsoft Graph integration — live-tested against a real developer tenant | Done |

77 automated tests pass against the simulated services. Every phase has also
been spot-checked against REQUIREMENTS.md and synthetic_corpus/, with Phase
12 additionally validated against a real Microsoft 365 developer tenant.

## Phase 1 — Load and validate

Reads the three files under `synthetic_corpus/` and reports structural
problems (bad dates, missing columns) instead of dropping or guessing at them.

## Phase 2 — Business decisions

Turns loaded data into a per-request decision; no external systems touched:

- **Scope**: only `Regular Full-Time` is in scope; everything else is flagged.
- **Duplicates**: a second request for the same Employee ID + Start Date is
  flagged; the earlier one is not.
- **Scheduling**: onboarding date is one business day before Start Date, moved
  back over weekends and company holidays.
- **Late detection**: flagged if HR marked the employee ready after the
  onboarding date had already passed.
- **Group mapping**: Department + Job Role is looked up in the mapping
  spreadsheet; unmapped combinations are flagged, not guessed at.
- **Corporate card**: `Yes`/`No` map to setup-required/no-action; anything
  else (blank, `Pending`, `Y`, etc.) is flagged for manual review.

## Phase 3 — Entra ID account step

Implements REQUIREMENTS.md's first onboarding step: create the Entra ID
account and verify it corresponds to the intended employee, using the
standard UPN format `firstname.lastname@<domain>` (configurable; defaults to
the synthetic `company.example`).

For a given request, the step produces one of:

- **`created`** — no account existed at the expected UPN; one was created. Verified.
- **`already_exists`** — same UPN, same employee ID (e.g. a rerun after a
  partial failure). Nothing recreated. Verified.
- **`manual_review`, UPN collision** — UPN belongs to a *different* employee
  ID; no alternative UPN is invented, per REQUIREMENTS.md.
- **`manual_review`, ambiguous** — an account exists at that UPN with no
  employee ID on record, so it can't be confirmed whose it is.

## Phase 4–5 — Licensing and group assignment

Same shape as Phase 3: assign Microsoft 365 E3 and the standard groups (from
the Phase 2 mapping). Both recognize existing state instead of reassigning,
verify the result rather than trusting the write, and route anything
nonstandard — an unrecognized existing license, an unmapped role, a
membership that doesn't verify — to manual review or failure instead of
guessing.

## Phase 6–11 — Tying it together

- **Workflow**: Runs the onboarding steps independently so one failure
  doesn't block or roll back successful work.
- **Audit**: Appends a structured record for every initial attempt and
  rerun without overwriting previous evidence.
- **Notifications**: Sends the simulated manager completion notification
  when onboarding succeeds, or an IT alert when intervention is required.
- **Processing**: Handles initial attempts, reruns, and batch processing
  while isolating unexpected failures between requests.

## Phase 12 — Real Microsoft Graph integration

`graph_client.py` handles app-only Microsoft Graph authentication, REST
calls, and exact-match identity resolution. `graph_services.py` provides
Graph-backed Entra ID, licensing, and group services using the same
interfaces as the simulated services from Phases 3–5. This allows the
existing business logic to run against a real tenant without being
rewritten.

**Live-tested**, not just unit-tested: `run_graph_demo.py` ran a real
onboarding attempt (`ONB-1001`) against a real Microsoft 365 developer
tenant, both an initial attempt and a rerun, through the unmodified
`process_onboarding` path. The tenant's Entra account and group memberships
verified successfully; the E3 license step correctly reported manual review,
since that tenant only has an E5 Developer subscription — REQUIREMENTS.md's
E3 requirement was deliberately left as-is rather than substituted, so this
is a documented environment limitation, not a bug.

Requires an app registration with these Graph **application** permissions
(least-privilege, admin-consented): `User.Create`, `User.Read.All`,
`LicenseAssignment.ReadWrite.All`, `LicenseAssignment.Read.All`,
`GroupMember.ReadWrite.All`. Credentials come from environment variables only
(`GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`, `GRAPH_CLIENT_SECRET`, and optionally
`GRAPH_UPN_DOMAIN`) — never hardcoded, never read from a committed file.

## Current limitations and scope

- **Corporate-card integration** — REQUIREMENTS.md notes the mechanism is
  still unresolved; a required card is recorded as pending, never attempted
  or faked.
- Any HR data source beyond the static CSV/XLSX files in `synthetic_corpus/`.
- Audit storage location and retention beyond a local JSONL file (also
  unresolved per REQUIREMENTS.md).
- Anything that schedules or triggers `process_batch` automatically — it's a
  function you call, not a running service.
- Contractors, interns, temps, rehires, and transfers — explicitly out of
  scope per REQUIREMENTS.md.

## Repo layout

```
REQUIREMENTS.md      Authoritative business requirements
onboarding.py          Phase 1 (load) + Phase 2 (decisions)
entra.py                Phase 3 (Entra account step + simulated directory)
licensing.py             Phase 4 (E3 licensing step + simulated service)
groups.py                Phase 5 (group assignment step + simulated service)
workflow.py               Phase 6 (combines the steps for one request)
audit.py                   Phase 7 (audit evidence log)
notifications.py            Phase 8 (manager email / IT alert, simulated)
process.py                   Phases 9-11 (rerun, one/many-request entry points)
graph_client.py                Phase 12 (Graph auth + REST + identity resolution)
graph_services.py                Phase 12 (real Graph-backed services)
run.py                             Runs Phases 1-2, prints a summary
run_graph_demo.py                   Live proof against a real tenant
tests/                                pytest suite (simulated services only)
synthetic_corpus/                      Fictional HR data used for development and testing
```

## Running it

```
pip install -r requirements.txt
pytest                # run the test suite (simulated only, no credentials needed)
python run.py          # Phases 1-2 summary
python run_graph_demo.py   # live proof against a real tenant (needs Graph credentials)
```

## Data

`synthetic_corpus/` is fictional data for this exercise (see its own
[README](synthetic_corpus/README.md)). It deliberately includes edge cases —
unmapped roles, duplicate requests, invalid corporate-card values, name
collisions — to exercise the manual-review paths, not just the happy path.
