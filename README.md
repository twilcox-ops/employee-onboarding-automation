# Employee Onboarding Automation

Automates routine employee onboarding: verifies required setup steps and routes
nonstandard or ambiguous cases to IT for manual review instead of guessing.
Full business rules live in [`REQUIREMENTS.md`](REQUIREMENTS.md); this README
summarizes what's actually built.

## Status

Phases 1–5 are implemented, tested, and committed. Nothing beyond that exists yet.

| Phase | Covers | Status |
|---|---|---|
| 1 | Load and validate the three source files | Done |
| 2 | Business decisions (scope, scheduling, group mapping, corporate-card validation, duplicates) | Done |
| 3 | Entra ID account step, against a simulated directory | Done |
| 4 | Microsoft 365 E3 licensing, against a simulated service | Done |
| 5 | Standard group assignment, against a simulated service | Done |
| 6+ | Corporate card setup, manager notification, orchestration, audit logging | Not started |

47 automated tests pass. Phases 2–5 were also spot-checked by hand against
`REQUIREMENTS.md` and `synthetic_corpus/`.

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
standard UPN format `firstname.lastname@company.example`.

**This runs against a `SimulatedEntraService`, not real Microsoft Entra ID** —
an in-memory dictionary keyed by UPN, standing in for a directory until a
real integration exists. It's not a mock of the Graph API — no tenants,
auth, or network calls — just somewhere concrete for the decision logic to
create and look up accounts.

For a given request, the step produces one of:

- **`created`** — no account existed at the expected UPN; one was created. Verified.
- **`already_exists`** — same UPN, same employee ID (e.g. a rerun after a
  partial failure). Nothing recreated. Verified.
- **`manual_review`, UPN collision** — UPN belongs to a *different* employee
  ID; no alternative UPN is invented, per REQUIREMENTS.md.
- **`manual_review`, ambiguous** — an account exists at that UPN with no
  employee ID on record, so it can't be confirmed whose it is.

Not wired into scope/duplicate checks or any other orchestration yet — it
only runs when called directly.

## Phase 4–5 — Licensing and group assignment

Same shape as Phase 3: `licensing.py` and `groups.py` assign Microsoft 365 E3
and the standard groups (from the Phase 2 mapping) against simulated
in-memory services. Both recognize existing state instead of reassigning,
verify the result rather than trusting the write, and route anything
nonstandard — an unrecognized existing license, an unmapped role, a
membership that doesn't verify — to manual review or failure instead of
guessing. Neither is wired into orchestration yet.

## Not implemented

Beyond Phases 1–5: corporate-card setup, manager lookup/notification, real
Entra/Graph and M365 integration, rerun handling beyond each step's own
logic, audit logging, cross-phase orchestration, Docker/deployment, and any
HR data source beyond the static CSV/XLSX files in `synthetic_corpus/`.
Contractors, interns, temps, rehires, and transfers are explicitly out of
scope per REQUIREMENTS.md.

## Repo layout

```
REQUIREMENTS.md    Authoritative business requirements
onboarding.py       Phase 1 (load) + Phase 2 (decisions)
entra.py             Phase 3 (Entra account step + simulated directory)
licensing.py         Phase 4 (E3 licensing step + simulated service)
groups.py            Phase 5 (group assignment step + simulated service)
run.py               Runs Phases 1-2, prints a summary
tests/                pytest suite
synthetic_corpus/     Fictional HR data used for development and testing
```

## Running it

```
pip install -r requirements.txt
pytest                # run the test suite
python run.py          # Phases 1-2 summary
```

`run.py` doesn't exercise Phases 3–5 yet — those steps are only called
directly (see `tests/test_entra.py`, `tests/test_licensing.py`,
`tests/test_groups.py`).

## Data

`synthetic_corpus/` is fictional data for this exercise (see its own
[README](synthetic_corpus/README.md)). It deliberately includes edge cases —
unmapped roles, duplicate requests, invalid corporate-card values, name
collisions — to exercise the manual-review paths, not just the happy path.
