"""Stage 11 tests: batch processing over process_onboarding. These only
prove the batch loop's own job — one result per request, one failure not
stopping the rest — not any business logic already covered by
tests/test_process.py and below. No Graph, Docker, scheduling, or
concurrency is exercised here.
"""

import datetime

from entra import SimulatedEntraService
from groups import SimulatedGroupService
from licensing import SimulatedLicensingService
from notifications import SimulatedNotifier
from onboarding import NewHireRequest, RequestDecision
from process import process_batch

MAPPING = {("Finance", "Financial Analyst"): ["Finance-Users", "Finance-Shared"]}
T1 = datetime.datetime(2026, 9, 15, 9, 0, 0)
T2 = datetime.datetime(2026, 9, 16, 9, 0, 0)


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


def new_services():
    return SimulatedEntraService(), SimulatedLicensingService(), SimulatedGroupService(), SimulatedNotifier()


class _RaisingEntraService(SimulatedEntraService):
    """Raises when creating an account for one specific employee,
    simulating an unexpected crash in an otherwise-working directory —
    used only to prove the batch continues past one item's exception."""

    def __init__(self, raise_for_employee_id):
        super().__init__()
        self._raise_for = raise_for_employee_id

    def create_account(self, upn, employee_id, first_name, last_name):
        if employee_id == self._raise_for:
            raise RuntimeError(f"simulated directory crash for {employee_id}")
        return super().create_account(upn, employee_id, first_name, last_name)


# --- multiple employees, each independently gets a result --------------------

def test_multiple_employees_each_get_their_own_result(tmp_path):
    audit_path = tmp_path / "audit_log.jsonl"
    entra, license_, groups, notifier = new_services()
    items = [
        (make_request(request_id="ONB-1", employee_id="EMP-1", first_name="Alex", last_name="Carter"),
         make_decision(request_id="ONB-1", employee_id="EMP-1")),
        (make_request(request_id="ONB-2", employee_id="EMP-2", first_name="Jordan", last_name="Brooks"),
         make_decision(request_id="ONB-2", employee_id="EMP-2")),
        (make_request(request_id="ONB-3", employee_id="EMP-3", first_name="Taylor", last_name="Bailey"),
         make_decision(request_id="ONB-3", employee_id="EMP-3")),
    ]

    results = process_batch(items, entra, license_, groups, notifier, MAPPING, T1, audit_log_path=audit_path)

    assert [r.request_id for r in results] == ["ONB-1", "ONB-2", "ONB-3"]
    assert all(r.succeeded for r in results)
    assert all(r.process_result.onboarding_result.completed for r in results)
    assert len(notifier.sent) == 3  # one per request


# --- mixed success/failure within one batch -----------------------------------

def test_mixed_success_and_incomplete_are_independent(tmp_path):
    audit_path = tmp_path / "audit_log.jsonl"
    entra, license_, groups, notifier = new_services()
    items = [
        (make_request(request_id="ONB-1", employee_id="EMP-1", first_name="Alex", last_name="Carter"),
         make_decision(request_id="ONB-1", employee_id="EMP-1")),
        (make_request(request_id="ONB-2", employee_id="EMP-2", first_name="Jordan", last_name="Brooks",
                       corporate_card_required="Yes"),
         make_decision(request_id="ONB-2", employee_id="EMP-2", card_action="setup_required")),
        (make_request(request_id="ONB-3", employee_id="EMP-3", first_name="Taylor", last_name="Bailey",
                       department="Research", job_role="Research Analyst"),
         make_decision(request_id="ONB-3", employee_id="EMP-3", required_groups=None)),
    ]

    results = process_batch(items, entra, license_, groups, notifier, MAPPING, T1, audit_log_path=audit_path)

    by_id = {r.request_id: r for r in results}
    assert by_id["ONB-1"].process_result.onboarding_result.completed is True
    assert by_id["ONB-2"].process_result.onboarding_result.completed is False
    assert by_id["ONB-2"].process_result.audit_record["overall_result"] == "incomplete_failed"
    assert by_id["ONB-3"].process_result.onboarding_result.completed is False
    assert by_id["ONB-3"].process_result.audit_record["overall_result"] == "manual_review"
    # ONB-1's success isn't affected by the other two.
    assert by_id["ONB-1"].process_result.onboarding_result.entra.verified is True


# --- mixed initial/rerun within one batch --------------------------------------

def test_mixed_initial_and_rerun_within_one_batch(tmp_path):
    audit_path = tmp_path / "audit_log.jsonl"
    entra, license_, groups, notifier = new_services()

    request_a = make_request(request_id="ONB-A", employee_id="EMP-A", first_name="Alex", last_name="Carter")
    decision_a = make_decision(request_id="ONB-A", employee_id="EMP-A")
    process_batch([(request_a, decision_a)], entra, license_, groups, notifier, MAPPING, T1, audit_log_path=audit_path)

    request_b = make_request(request_id="ONB-B", employee_id="EMP-B", first_name="Jordan", last_name="Brooks")
    decision_b = make_decision(request_id="ONB-B", employee_id="EMP-B")

    results = process_batch(
        [(request_a, decision_a), (request_b, decision_b)],  # A reruns, B is new
        entra, license_, groups, notifier, MAPPING, T2, audit_log_path=audit_path,
    )

    by_id = {r.request_id: r for r in results}
    assert by_id["ONB-A"].process_result.audit_record["attempt_type"] == "rerun"
    assert by_id["ONB-A"].process_result.onboarding_result.entra.status == "already_exists"
    assert by_id["ONB-B"].process_result.audit_record["attempt_type"] == "initial"


# --- one unexpected exception doesn't stop the batch --------------------------

def test_one_unexpected_exception_does_not_stop_the_batch(tmp_path):
    audit_path = tmp_path / "audit_log.jsonl"
    entra = _RaisingEntraService(raise_for_employee_id="EMP-2")
    license_, groups, notifier = SimulatedLicensingService(), SimulatedGroupService(), SimulatedNotifier()
    items = [
        (make_request(request_id="ONB-1", employee_id="EMP-1", first_name="Alex", last_name="Carter"),
         make_decision(request_id="ONB-1", employee_id="EMP-1")),
        (make_request(request_id="ONB-2", employee_id="EMP-2", first_name="Jordan", last_name="Brooks"),
         make_decision(request_id="ONB-2", employee_id="EMP-2")),
        (make_request(request_id="ONB-3", employee_id="EMP-3", first_name="Taylor", last_name="Bailey"),
         make_decision(request_id="ONB-3", employee_id="EMP-3")),
    ]

    results = process_batch(items, entra, license_, groups, notifier, MAPPING, T1, audit_log_path=audit_path)

    assert [r.request_id for r in results] == ["ONB-1", "ONB-2", "ONB-3"]

    by_id = {r.request_id: r for r in results}
    assert by_id["ONB-1"].succeeded is True
    assert by_id["ONB-3"].succeeded is True  # batch continued past ONB-2

    assert by_id["ONB-2"].succeeded is False
    assert by_id["ONB-2"].process_result is None
    assert "simulated directory crash" in by_id["ONB-2"].error

    # ONB-1 and ONB-3 were fully processed and notified despite ONB-2's crash.
    assert len(notifier.sent) == 2
