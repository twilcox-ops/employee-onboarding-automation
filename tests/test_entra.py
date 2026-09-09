"""Stage 3 tests: the Entra ID account step, run against the simulated
in-memory directory. No real Entra, licensing, groups, corporate card,
notifications, or orchestration is exercised here.
"""

import datetime

from entra import (
    ENTRA_STATUS_ALREADY_EXISTS,
    ENTRA_STATUS_CREATED,
    ENTRA_STATUS_MANUAL_REVIEW,
    SimulatedEntraService,
    evaluate_entra_account_step,
    expected_upn,
)
from onboarding import NewHireRequest


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


# --- UPN format -------------------------------------------------------------

def test_expected_upn_standard_format():
    # REQUIREMENTS.md: firstname.lastname@company.example.
    assert expected_upn("Morgan", "Bennett") == "morgan.bennett@company.example"


def test_expected_upn_is_lowercased():
    assert expected_upn("Taylor", "Bailey") == "taylor.bailey@company.example"


# --- creating a new account --------------------------------------------------

def test_creates_account_when_none_exists():
    service = SimulatedEntraService()
    request = make_request(employee_id="EMP-3001", first_name="Alex", last_name="Carter")

    result = evaluate_entra_account_step(request, service)

    assert result.status == ENTRA_STATUS_CREATED
    assert result.verified is True
    assert result.upn == "alex.carter@company.example"
    assert result.manual_review_reason is None

    created = service.find_by_upn("alex.carter@company.example")
    assert created is not None
    assert created.employee_id == "EMP-3001"


# --- detecting an existing matching account (rerun-safe) ---------------------

def test_detects_existing_account_for_same_employee_as_already_exists():
    service = SimulatedEntraService()
    service.seed_account("alex.carter@company.example", employee_id="EMP-3001",
                          first_name="Alex", last_name="Carter")
    request = make_request(employee_id="EMP-3001", first_name="Alex", last_name="Carter")

    result = evaluate_entra_account_step(request, service)

    assert result.status == ENTRA_STATUS_ALREADY_EXISTS
    assert result.verified is True
    assert result.manual_review_reason is None


def test_rerun_after_first_call_does_not_recreate_or_raise():
    # Simulates IT rerunning a case: the step already succeeded once, so the
    # second run should find the same account rather than trying (and
    # failing) to create a duplicate.
    service = SimulatedEntraService()
    request = make_request(employee_id="EMP-3001", first_name="Alex", last_name="Carter")

    first = evaluate_entra_account_step(request, service)
    second = evaluate_entra_account_step(request, service)

    assert first.status == ENTRA_STATUS_CREATED
    assert second.status == ENTRA_STATUS_ALREADY_EXISTS
    assert second.verified is True


# --- UPN collision (different employee already holds the UPN) ---------------

def test_upn_collision_with_different_employee_routes_to_manual_review():
    service = SimulatedEntraService()
    service.seed_account("alex.carter@company.example", employee_id="EMP-9999",
                          first_name="Alex", last_name="Carter")
    request = make_request(employee_id="EMP-3001", first_name="Alex", last_name="Carter")

    result = evaluate_entra_account_step(request, service)

    assert result.status == ENTRA_STATUS_MANUAL_REVIEW
    assert result.verified is False
    assert "EMP-9999" in result.manual_review_reason
    assert "alex.carter@company.example" in result.manual_review_reason

    # REQUIREMENTS.md: do not invent an alternative UPN — no new account
    # should have been created anywhere as a side effect.
    assert service.find_by_upn("alex.carter@company.example").employee_id == "EMP-9999"


# --- ambiguous existing account (can't confirm whose it is) -----------------

def test_ambiguous_existing_account_with_no_employee_id_routes_to_manual_review():
    service = SimulatedEntraService()
    service.seed_account("alex.carter@company.example", employee_id=None,
                          first_name="Alex", last_name="Carter")
    request = make_request(employee_id="EMP-3001", first_name="Alex", last_name="Carter")

    result = evaluate_entra_account_step(request, service)

    assert result.status == ENTRA_STATUS_MANUAL_REVIEW
    assert result.verified is False
    assert "cannot be confirmed" in result.manual_review_reason


def test_ambiguous_existing_account_with_blank_employee_id_routes_to_manual_review():
    service = SimulatedEntraService()
    service.seed_account("alex.carter@company.example", employee_id="",
                          first_name="Alex", last_name="Carter")
    request = make_request(employee_id="EMP-3001", first_name="Alex", last_name="Carter")

    result = evaluate_entra_account_step(request, service)

    assert result.status == ENTRA_STATUS_MANUAL_REVIEW
    assert result.verified is False


# --- the simulated service itself -------------------------------------------

def test_create_account_raises_on_duplicate_upn():
    service = SimulatedEntraService()
    service.create_account("alex.carter@company.example", "EMP-3001", "Alex", "Carter")

    try:
        service.create_account("alex.carter@company.example", "EMP-9999", "Alex", "Carter")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_find_by_upn_returns_none_when_absent():
    service = SimulatedEntraService()
    assert service.find_by_upn("nobody.here@company.example") is None
