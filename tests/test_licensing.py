"""Stage 4 tests: the M365 E3 licensing step, run against the simulated
in-memory service. No real Graph, group assignment, corporate card,
notifications, or orchestration is exercised here.
"""

import datetime

from licensing import (
    E3_SKU,
    LICENSE_STATUS_ALREADY_ASSIGNED,
    LICENSE_STATUS_ASSIGNED,
    LICENSE_STATUS_MANUAL_REVIEW,
    SimulatedLicensingService,
    evaluate_license_step,
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


class _AssignNoOpService:
    """Stub simulating a backend where assignment silently fails to take
    effect, to check evaluate_license_step verifies rather than trusting
    the assign call."""

    def get_licenses(self, employee_id):
        return frozenset()

    def assign_license(self, employee_id, sku):
        pass  # intentionally does nothing


# --- assigning when none exists ---------------------------------------------

def test_assigns_e3_when_none_on_record():
    service = SimulatedLicensingService()
    request = make_request(employee_id="EMP-3001")

    result = evaluate_license_step(request, service)

    assert result.status == LICENSE_STATUS_ASSIGNED
    assert result.verified is True
    assert result.sku == E3_SKU
    assert result.manual_review_reason is None
    assert result.current_licenses == frozenset({E3_SKU})
    assert E3_SKU in service.get_licenses("EMP-3001")


# --- recognizing an already-assigned license ---------------------------------

def test_recognizes_already_assigned_e3():
    service = SimulatedLicensingService()
    service.seed_licenses("EMP-3001", [E3_SKU])
    request = make_request(employee_id="EMP-3001")

    result = evaluate_license_step(request, service)

    assert result.status == LICENSE_STATUS_ALREADY_ASSIGNED
    assert result.verified is True
    assert result.manual_review_reason is None
    assert result.current_licenses == frozenset({E3_SKU})


def test_e3_already_assigned_alongside_another_license_is_recognized():
    service = SimulatedLicensingService()
    service.seed_licenses("EMP-3001", [E3_SKU, "Visio Plan 2"])
    request = make_request(employee_id="EMP-3001")

    result = evaluate_license_step(request, service)

    assert result.status == LICENSE_STATUS_ALREADY_ASSIGNED
    assert result.verified is True
    assert result.current_licenses == frozenset({E3_SKU, "Visio Plan 2"})


def test_rerun_after_first_call_does_not_reassign_or_raise():
    service = SimulatedLicensingService()
    request = make_request(employee_id="EMP-3001")

    first = evaluate_license_step(request, service)
    second = evaluate_license_step(request, service)

    assert first.status == LICENSE_STATUS_ASSIGNED
    assert second.status == LICENSE_STATUS_ALREADY_ASSIGNED
    assert second.verified is True


# --- nonstandard existing license state --------------------------------------

def test_unexpected_existing_license_without_e3_routes_to_manual_review():
    service = SimulatedLicensingService()
    service.seed_licenses("EMP-3001", ["Visio Plan 2"])
    request = make_request(employee_id="EMP-3001")

    result = evaluate_license_step(request, service)

    assert result.status == LICENSE_STATUS_MANUAL_REVIEW
    assert result.verified is False
    assert "Visio Plan 2" in result.manual_review_reason

    # Nothing should have been assigned as a side effect.
    assert E3_SKU not in service.get_licenses("EMP-3001")


def test_manual_review_sku_is_target_not_actual_current_license():
    # sku is always what we're trying to assign; current_licenses is the
    # structured form of what the employee actually has.
    service = SimulatedLicensingService()
    service.seed_licenses("EMP-3001", ["Project Plan 3"])
    request = make_request(employee_id="EMP-3001")

    result = evaluate_license_step(request, service)

    assert result.sku == E3_SKU
    assert result.current_licenses == frozenset({"Project Plan 3"})


# --- verification of the final state -----------------------------------------

def test_assignment_that_does_not_take_effect_routes_to_manual_review():
    request = make_request(employee_id="EMP-3001")

    result = evaluate_license_step(request, _AssignNoOpService())

    assert result.status == LICENSE_STATUS_MANUAL_REVIEW
    assert result.verified is False
    assert "did not verify" in result.manual_review_reason
    assert result.current_licenses == frozenset()


# --- the simulated service itself -------------------------------------------

def test_get_licenses_empty_when_none_assigned():
    service = SimulatedLicensingService()
    assert service.get_licenses("EMP-3001") == frozenset()


def test_seed_licenses_sets_exact_state():
    service = SimulatedLicensingService()
    service.seed_licenses("EMP-3001", [E3_SKU, "Visio Plan 2"])
    assert service.get_licenses("EMP-3001") == frozenset({E3_SKU, "Visio Plan 2"})
