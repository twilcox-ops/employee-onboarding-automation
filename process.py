"""Stage 10: top-level onboarding processing.

One entry point that connects the existing pieces into a complete
simulated onboarding attempt for one already-validated request: Stage 9's
rerun-aware workflow + audit logging, then Stage 8's notification
handling. This module adds no business logic of its own — it only calls
`rerun.run_onboarding_attempt` (which already calls Stage 6's workflow and
Stage 7's audit logging, and already preserves initial/rerun and
continue-on-independent-failure behavior) and then
`notifications.notify_for_attempt` (which already implements the manager
completion email vs. IT notification rules) with the result.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from pathlib import Path

from audit import DEFAULT_AUDIT_LOG_PATH
from entra import SimulatedEntraService
from groups import SimulatedGroupService
from licensing import SimulatedLicensingService
from notifications import NotificationOutcome, SimulatedNotifier, notify_for_attempt
from onboarding import NewHireRequest, RequestDecision
from rerun import run_onboarding_attempt
from workflow import OnboardingResult


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
) -> ProcessResult:
    """Run and record one onboarding attempt end to end, initial or rerun
    alike, and notify accordingly.

    Steps (each already implemented elsewhere, just called in order):
    1. `rerun.run_onboarding_attempt` — determines initial vs. rerun from
       the audit log, runs the Stage 6 workflow against live simulated
       state, and appends the Stage 7 audit record.
    2. `notifications.notify_for_attempt` — sends the manager completion
       email if every step verified and the manager is uniquely
       identified, otherwise one IT notification.
    """
    onboarding_result, audit_record = run_onboarding_attempt(
        request, decision, entra_service, license_service, group_service,
        mapping, run_timestamp, audit_log_path=audit_log_path,
    )
    notification_outcome = notify_for_attempt(request, onboarding_result, entra_service, notifier)

    return ProcessResult(
        request_id=request.request_id,
        employee_id=request.employee_id,
        onboarding_result=onboarding_result,
        audit_record=audit_record,
        notification_outcome=notification_outcome,
    )
