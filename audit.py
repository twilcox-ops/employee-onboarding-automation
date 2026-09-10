"""Stage 7: the first audit/evidence step.

REQUIREMENTS.md, Audit / Evidence: each onboarding attempt must retain
enough information for IT to see what was attempted, what succeeded or
failed, and when, without reconstructing it from separate systems — a
record that a step verified successfully is enough, a dump of API
responses is not required.

This builds one structured record per attempt from the existing Stage 2
decision and Stage 6 workflow result, reusing their fields rather than
recomputing anything, and appends it as one JSON line to a log file —
never overwriting prior records. Storage location/retention beyond a local
file, real rerun execution, and IT notification are not decided/built yet.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any

from onboarding import NewHireRequest, RequestDecision
from workflow import OnboardingResult

DEFAULT_AUDIT_LOG_PATH = Path("audit_log.jsonl")


def _step_entry(name: str, status: str, verified: bool, reason: str | None) -> dict[str, Any]:
    return {"step": name, "status": status, "verified": verified, "reason": reason}


def build_audit_record(
    request: NewHireRequest,
    decision: RequestDecision,
    workflow_result: OnboardingResult,
    run_timestamp: datetime.datetime,
    is_rerun: bool,
) -> dict[str, Any]:
    """Assemble one audit record for a single onboarding attempt.

    Overall result is "completed" if every step verified; otherwise
    "manual_review" if any step (or the Stage 2 decision itself) flagged a
    manual-review reason, else "incomplete_failed" — e.g. a corporate card
    that's required but has no integration to attempt yet.
    """
    steps = {
        "entra_account": _step_entry(
            "entra_account", workflow_result.entra.status,
            workflow_result.entra.verified, workflow_result.entra.manual_review_reason,
        ),
        "license": _step_entry(
            "license", workflow_result.license.status,
            workflow_result.license.verified, workflow_result.license.manual_review_reason,
        ),
        "groups": _step_entry(
            "groups", workflow_result.groups.status,
            workflow_result.groups.verified, workflow_result.groups.manual_review_reason,
        ),
        "corporate_card": _step_entry(
            "corporate_card", workflow_result.card_status,
            "corporate_card" not in workflow_result.failed_steps, workflow_result.card_note,
        ),
    }

    manual_review_reasons = list(decision.manual_review_reasons)
    for name, entry in steps.items():
        if entry["status"] == "manual_review" and entry["reason"]:
            manual_review_reasons.append(f"{name}: {entry['reason']}")

    if workflow_result.completed:
        overall_result = "completed"
    elif manual_review_reasons:
        overall_result = "manual_review"
    else:
        overall_result = "incomplete_failed"

    return {
        "request_id": workflow_result.request_id,
        "employee_id": workflow_result.employee_id,
        "employee_name": f"{request.first_name} {request.last_name}",
        "start_date": request.start_date.isoformat() if request.start_date else None,
        "run_timestamp": run_timestamp.isoformat(),
        "attempt_type": "rerun" if is_rerun else "initial",
        "late": decision.is_late,
        "steps": steps,
        "overall_result": overall_result,
        "failed_steps": [steps[name] for name in workflow_result.failed_steps],
        "manual_review_reasons": manual_review_reasons,
    }


def append_audit_record(record: dict[str, Any], path: str | Path = DEFAULT_AUDIT_LOG_PATH) -> None:
    """Append one record as a JSON line. Only ever appends — never opens
    for write/truncate — so prior records are preserved."""
    with Path(path).open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def record_onboarding_attempt(
    request: NewHireRequest,
    decision: RequestDecision,
    workflow_result: OnboardingResult,
    run_timestamp: datetime.datetime,
    is_rerun: bool,
    path: str | Path = DEFAULT_AUDIT_LOG_PATH,
) -> dict[str, Any]:
    """Build and append one audit record in a single call; returns the
    record that was written."""
    record = build_audit_record(request, decision, workflow_result, run_timestamp, is_rerun)
    append_audit_record(record, path)
    return record
