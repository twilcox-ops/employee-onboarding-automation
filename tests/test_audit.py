"""Stage 7 tests: building and appending audit records. No real rerun
execution, IT notification, or manager email is exercised here.
"""

import datetime
import json

from audit import append_audit_record, build_audit_record, record_onboarding_attempt
from entra import SimulatedEntraService
from groups import SimulatedGroupService
from licensing import SimulatedLicensingService
from onboarding import NewHireRequest, RequestDecision
from workflow import run_onboarding

MAPPING = {("Finance", "Financial Analyst"): ["Finance-Users", "Finance-Shared"]}
RUN_TIMESTAMP = datetime.datetime(2026, 9, 16, 9, 0, 0)


def make_request(**overrides):
    defaults = dict(
        request_id="ONB-TEST",
        employee_id="EMP-TEST",
        first_name="Test",
        last_name="Person",
        department="Finance",
        job_role="Financial Analyst",
        manager="Some Manager",
        employment_type="Regular Full-Time",
        start_date=datetime.date(2026, 9, 17),
        corporate_card_required="No",
        ready_for_onboarding="Yes",
        hr_ready_date=datetime.date(2026, 9, 11),
    )
    defaults.update(overrides)
    return NewHireRequest(**defaults)


def make_decision(**overrides):
    defaults = dict(
        request_id="ONB-TEST", employee_id="EMP-TEST", in_scope=True, is_duplicate=False,
        onboarding_date=datetime.date(2026, 9, 16), is_late=False,
        required_groups=["Finance-Users", "Finance-Shared"], card_action="no_action",
        manual_review_reasons=[],
    )
    defaults.update(overrides)
    return RequestDecision(**defaults)


def run_workflow(request):
    return run_onboarding(
        request, SimulatedEntraService(), SimulatedLicensingService(),
        SimulatedGroupService(), MAPPING,
    )


# --- initial attempt ---------------------------------------------------------

def test_initial_attempt_record_has_required_fields():
    request = make_request()
    decision = make_decision(is_late=True)
    result = run_workflow(request)

    record = build_audit_record(request, decision, result, RUN_TIMESTAMP, is_rerun=False)

    assert record["employee_id"] == "EMP-TEST"
    assert record["employee_name"] == "Test Person"
    assert record["start_date"] == "2026-09-17"
    assert record["run_timestamp"] == "2026-09-16T09:00:00"
    assert record["attempt_type"] == "initial"
    assert record["late"] is True
    assert record["overall_result"] == "completed"
    assert record["failed_steps"] == []
    assert record["manual_review_reasons"] == []
    assert set(record["steps"]) == {"entra_account", "license", "groups", "corporate_card"}
    assert record["steps"]["entra_account"]["verified"] is True


# --- rerun appends a second record, nothing overwritten ----------------------

def test_rerun_appends_second_record_without_overwriting(tmp_path):
    log_path = tmp_path / "audit_log.jsonl"
    request = make_request()
    decision = make_decision()

    record_onboarding_attempt(
        request, decision, run_workflow(request), RUN_TIMESTAMP, is_rerun=False, path=log_path,
    )
    record_onboarding_attempt(
        request, decision, run_workflow(request), RUN_TIMESTAMP, is_rerun=True, path=log_path,
    )

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2

    first, second = (json.loads(line) for line in lines)
    assert first["attempt_type"] == "initial"
    assert second["attempt_type"] == "rerun"
    assert first["employee_id"] == second["employee_id"] == "EMP-TEST"


def test_append_audit_record_never_truncates_existing_lines(tmp_path):
    log_path = tmp_path / "audit_log.jsonl"
    log_path.write_text('{"pre_existing": true}\n', encoding="utf-8")

    append_audit_record({"new": True}, path=log_path)

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == {"pre_existing": True}
    assert json.loads(lines[1]) == {"new": True}


# --- failed step records what failed and why ---------------------------------

def test_failed_step_is_recorded_with_status_and_reason():
    # Unmapped department/role fails the group step.
    request = make_request(department="Research", job_role="Research Analyst")
    decision = make_decision(required_groups=None)
    result = run_workflow(request)

    record = build_audit_record(request, decision, result, RUN_TIMESTAMP, is_rerun=False)

    assert record["overall_result"] == "manual_review"
    assert len(record["failed_steps"]) == 1
    failed = record["failed_steps"][0]
    assert failed["step"] == "groups"
    assert failed["status"] == "manual_review"
    assert failed["verified"] is False
    assert "Research" in failed["reason"]
    assert any("groups:" in reason for reason in record["manual_review_reasons"])


def test_card_required_with_no_integration_is_incomplete_not_manual_review():
    request = make_request(corporate_card_required="Yes")
    decision = make_decision(card_action="setup_required")
    result = run_workflow(request)

    record = build_audit_record(request, decision, result, RUN_TIMESTAMP, is_rerun=False)

    assert record["overall_result"] == "incomplete_failed"
    assert record["failed_steps"] == [
        {
            "step": "corporate_card", "status": "pending_integration",
            "verified": False, "reason": result.card_note,
        }
    ]
    assert record["manual_review_reasons"] == []
