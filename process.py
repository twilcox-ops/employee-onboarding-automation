"""Stages 9–11: run one onboarding attempt — or a batch of them — end to
end: rerun detection, the Stage 6 workflow, Stage 7 audit logging, and
Stage 8 notifications. (Formerly split across rerun.py/process.py/
batch.py; merged here since together they added only ~30 lines of real
logic around ~250 lines of parameter-threading and pass-through glue.
workflow.py stays a separate file: audit.py and notifications.py both
depend on its OnboardingResult type, and this module depends on audit.py
and notifications.py, so folding workflow.py in here too would make it
both a dependency of and a consumer of this file — a circular import.)

REQUIREMENTS.md, Duplicate Requests & Reruns: "After a failed onboarding,
IT must be able to rerun the case. On rerun, the automation checks which
required steps were already successfully completed and verified, and
leaves those unchanged — it continues only with the remaining incomplete
steps. If the automation cannot confidently determine whether a step was
already completed, it stops that case and sends it to IT for manual
review rather than guessing."

The rerun model here is deliberately simple, and deliberately does NOT
compare a rerun against what an earlier audit record claimed:

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
   — never merely because the live state differs from what an earlier
   attempt achieved or recorded.
6. Successful work is never rolled back.
7. Reruns stay automated by default; manual review is the exception.

Stage 3/4/5's step functions already satisfy all of this by construction
— each one re-checks live state on every call and never removes anything,
whether that call is the first attempt or the fifth. So calling
`workflow.run_onboarding` again against the same, persistent service
instances already gives every rule above, and there is no per-step
retry/skip/compare logic to write here. What this module adds is the one
thing the steps genuinely can't know on their own — whether this is the
first attempt for a case or a rerun of it — plus recording the result and
notifying, for one request or many.
"""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass
from pathlib import Path

from audit import DEFAULT_AUDIT_LOG_PATH, record_onboarding_attempt
from entra import SimulatedEntraService
from groups import SimulatedGroupService
from licensing import SimulatedLicensingService
from notifications import NotificationOutcome, SimulatedNotifier, notify_for_attempt
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


@dataclass
class ProcessResult:
    """The complete outcome of one onboarding attempt: what happened, what
    was recorded for audit, and what was notified."""
    request_id: str
    employee_id: str
    onboarding_result: OnboardingResult
    audit_record: dict
    notification_outcome: NotificationOutcome


def process_onboarding(
    request: NewHireRequest,
    decision: RequestDecision,
    entra_service: SimulatedEntraService,
    license_service: SimulatedLicensingService,
    group_service: SimulatedGroupService,
    notifier: SimulatedNotifier,
    mapping: dict[tuple[str, str], list[str]],
    run_timestamp: datetime.datetime,
    audit_log_path: str | Path = DEFAULT_AUDIT_LOG_PATH,
    domain: str = "company.example",
) -> ProcessResult:
    """Run and record one onboarding attempt end to end, initial or rerun
    alike, and notify accordingly.

    1. Look up initial vs. rerun from the audit log (the only use it gets).
    2. Run the Stage 6 workflow against live service state — correct
       whether this is attempt one or attempt five, since it always
       re-verifies rather than trusting anything cached.
    3. Append the Stage 7 audit record.
    4. Notify: the manager completion email if every step verified and
       the manager is uniquely identified, otherwise one IT notification
       (Stage 8) — including on a rerun that's still incomplete.
    """
    is_rerun = has_prior_attempt(request, audit_log_path)
    onboarding_result = run_onboarding(
        request, entra_service, license_service, group_service, mapping, domain,
    )
    audit_record = record_onboarding_attempt(
        request, decision, onboarding_result, run_timestamp, is_rerun, path=audit_log_path,
    )
    notification_outcome = notify_for_attempt(request, onboarding_result, entra_service, notifier)

    return ProcessResult(
        request_id=request.request_id,
        employee_id=request.employee_id,
        onboarding_result=onboarding_result,
        audit_record=audit_record,
        notification_outcome=notification_outcome,
    )


@dataclass
class BatchItemResult:
    """One request's outcome within a batch: either the full
    `ProcessResult`, or the error that stopped this one request without
    stopping the batch."""
    request_id: str
    employee_id: str
    process_result: ProcessResult | None
    error: str | None

    @property
    def succeeded(self) -> bool:
        return self.error is None


def process_batch(
    items: list[tuple[NewHireRequest, RequestDecision]],
    entra_service: SimulatedEntraService,
    license_service: SimulatedLicensingService,
    group_service: SimulatedGroupService,
    notifier: SimulatedNotifier,
    mapping: dict[tuple[str, str], list[str]],
    run_timestamp: datetime.datetime,
    audit_log_path: str | Path = DEFAULT_AUDIT_LOG_PATH,
    domain: str = "company.example",
) -> list[BatchItemResult]:
    """Process every (request, decision) pair independently via
    `process_onboarding`, sharing the same simulated services and audit
    log across the batch (the same directory/licenses/groups/log a real
    org would have one of). One request raising doesn't stop the rest —
    that request's `BatchItemResult` carries the error instead, and
    processing continues with the next request.
    """
    results: list[BatchItemResult] = []
    for request, decision in items:
        try:
            process_result = process_onboarding(
                request, decision, entra_service, license_service, group_service,
                notifier, mapping, run_timestamp, audit_log_path=audit_log_path, domain=domain,
            )
            results.append(
                BatchItemResult(request.request_id, request.employee_id, process_result, None)
            )
        except Exception as exc:  # noqa: BLE001 - deliberately broad: isolate one item's failure
            results.append(
                BatchItemResult(request.request_id, request.employee_id, None, str(exc))
            )
    return results
