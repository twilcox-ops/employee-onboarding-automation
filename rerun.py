"""Stage 9: rerun behavior.

REQUIREMENTS.md, Duplicate Requests & Reruns: "After a failed onboarding,
IT must be able to rerun the case. On rerun, the automation checks which
required steps were already successfully completed and verified, and
leaves those unchanged — it continues only with the remaining incomplete
steps. If the automation cannot confidently determine whether a step was
already completed, it stops that case and sends it to IT for manual
review rather than guessing."

The model this stage follows is deliberately simple, and deliberately
does NOT compare a rerun against what an earlier audit record claimed:

1. The audit log determines exactly one thing: initial vs. rerun. Nothing
   else about a rerun's behavior depends on audit history.
2. What's actually true right now comes only from the live simulated
   service state — never from what a prior attempt recorded.
3. If the desired state is known and the live state is wrong or missing,
   fix it (create the account / assign the SKU / add the group), then
   verify.
4. If the live state is already correct, verify it and leave it alone.
5. Manual review is used only when identity, the desired state, a
   mapping, or business intent is itself ambiguous or unknown right now
   (an unmapped role, no employee ID on an existing account, a UPN a
   different employee currently holds) — never merely because the live
   state differs from what an earlier attempt achieved or recorded.
6. Successful work is never rolled back.
7. Reruns stay automated by default; manual review is the exception, for
   genuine ambiguity, not the default response to change.

Stage 3/4/5's step functions (`evaluate_entra_account_step`,
`evaluate_license_step`, `evaluate_group_step`) already satisfy all of
this by construction: each one re-checks live state on every call and
never removes anything, whether that call is the first attempt or the
fifth. So calling `workflow.run_onboarding` again against the same,
persistent service instances already gives every rule above, and there is
no new per-step retry/skip/compare logic to write here. What this stage
actually adds is the one thing the steps genuinely can't know on their
own — whether this is the first attempt for a case or a rerun of it —
which is the only question the audit log answers.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

from audit import DEFAULT_AUDIT_LOG_PATH, record_onboarding_attempt
from entra import SimulatedEntraService
from groups import SimulatedGroupService
from licensing import SimulatedLicensingService
from onboarding import NewHireRequest, RequestDecision
from workflow import OnboardingResult, run_onboarding


def has_prior_attempt(request: NewHireRequest, audit_log_path: str | Path = DEFAULT_AUDIT_LOG_PATH) -> bool:
    """True if the audit log already holds at least one record for this
    same case — same employee and start date, REQUIREMENTS.md's own
    definition of "the same case" (see its duplicate-request rule).

    This is the *only* thing the audit log is used for here: identifying
    that a prior attempt happened, not what it supposedly achieved.
    """
    path = Path(audit_log_path)
    if not path.exists():
        return False

    start_date = request.start_date.isoformat() if request.start_date else None
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if record.get("employee_id") == request.employee_id and record.get("start_date") == start_date:
                return True
    return False


def run_onboarding_attempt(
    request: NewHireRequest,
    decision: RequestDecision,
    entra_service: SimulatedEntraService,
    license_service: SimulatedLicensingService,
    group_service: SimulatedGroupService,
    mapping: dict[tuple[str, str], list[str]],
    run_timestamp: datetime.datetime,
    audit_log_path: str | Path = DEFAULT_AUDIT_LOG_PATH,
) -> tuple[OnboardingResult, dict]:
    """Run one onboarding attempt — initial or rerun alike — and record it.

    `is_rerun` is looked up from the audit log *before* running anything,
    then the same `run_onboarding` used for a first attempt is called
    unchanged: it always re-verifies live state, so it's already correct
    whether this is attempt one or attempt five. The resulting audit
    record correctly marks initial vs. rerun without any of the step
    logic needing to know or care which one this is.
    """
    is_rerun = has_prior_attempt(request, audit_log_path)
    result = run_onboarding(request, entra_service, license_service, group_service, mapping)
    record = record_onboarding_attempt(
        request, decision, result, run_timestamp, is_rerun, path=audit_log_path,
    )
    return result, record
