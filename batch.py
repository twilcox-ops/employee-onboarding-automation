"""Stage 11: batch processing.

Runs Stage 10's `process_onboarding` over multiple already-validated
requests. This adds no business logic of its own beyond the loop itself:
each request still goes through the same rerun-aware workflow, audit
logging, and notification handling as a single call would, against the
same shared simulated services — so Entra/license/group state, the audit
log, and rerun detection all behave exactly as they do for one request,
just run for several in sequence.

The one thing this stage is actually responsible for: one request's
unhandled exception must not stop the rest of the batch, and every
request gets a result back — success or failure.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from pathlib import Path

from audit import DEFAULT_AUDIT_LOG_PATH
from entra import SimulatedEntraService
from groups import SimulatedGroupService
from licensing import SimulatedLicensingService
from notifications import SimulatedNotifier
from onboarding import NewHireRequest, RequestDecision
from process import ProcessResult, process_onboarding


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
                notifier, mapping, run_timestamp, audit_log_path=audit_log_path,
            )
            results.append(
                BatchItemResult(request.request_id, request.employee_id, process_result, None)
            )
        except Exception as exc:  # noqa: BLE001 - deliberately broad: isolate one item's failure
            results.append(
                BatchItemResult(request.request_id, request.employee_id, None, str(exc))
            )
    return results
