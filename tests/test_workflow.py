"""Stage 6 tests: the combined workflow that runs the existing Entra,
licensing, and group steps for one request. No card integration, IT
notification, audit storage, or manager email is exercised here.
"""

import datetime

from entra import SimulatedEntraService
from groups import SimulatedGroupService
from licensing import SimulatedLicensingService
from onboarding import NewHireRequest
from workflow import (
    CARD_STATUS_MANUAL_REVIEW,
    CARD_STATUS_NOT_APPLICABLE,
    CARD_STATUS_PENDING_INTEGRATION,
    run_onboarding,
)

MAPPING = {("Finance", "Financial Analyst"): ["Finance-Users", "Finance-Shared"]}


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


def run(request):
    return run_onboarding(
        request,
        SimulatedEntraService(),
        SimulatedLicensingService(),
        SimulatedGroupService(),
        MAPPING,
    )


def test_successful_onboarding_with_no_card_required():
    request = make_request(corporate_card_required="No")

    result = run(request)

    assert result.entra.verified is True
    assert result.license.verified is True
    assert result.groups.verified is True
    assert result.card_status == CARD_STATUS_NOT_APPLICABLE
    assert result.card_note is None
    assert result.completed is True
    assert result.failed_steps == []


def test_card_required_onboarding_remains_incomplete():
    request = make_request(corporate_card_required="Yes")

    result = run(request)

    # The other steps still succeed independently.
    assert result.entra.verified is True
    assert result.license.verified is True
    assert result.groups.verified is True

    assert result.card_status == CARD_STATUS_PENDING_INTEGRATION
    assert "not implemented yet" in result.card_note
    assert result.completed is False
    assert result.failed_steps == ["corporate_card"]


def test_missing_card_value_routes_to_manual_review_and_stays_incomplete():
    request = make_request(corporate_card_required="")

    result = run(request)

    assert result.card_status == CARD_STATUS_MANUAL_REVIEW
    assert "missing or invalid" in result.card_note
    assert result.completed is False
    assert "corporate_card" in result.failed_steps


def test_one_failed_step_does_not_block_or_roll_back_others():
    # Unmapped department/role fails the group step; Entra and licensing
    # are independent and should still succeed and stay in place.
    request = make_request(department="Research", job_role="Research Analyst")

    result = run(request)

    assert result.groups.verified is False
    assert result.entra.verified is True
    assert result.license.verified is True
    assert result.completed is False
    assert result.failed_steps == ["groups"]
