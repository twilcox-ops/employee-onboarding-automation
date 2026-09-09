"""Stage 5 tests: the standard group assignment step, run against the
simulated in-memory group service. No real Graph, corporate card,
notifications, or orchestration is exercised here.
"""

import datetime

from groups import (
    GROUP_STATUS_FAILED,
    GROUP_STATUS_MANUAL_REVIEW,
    GROUP_STATUS_VERIFIED,
    SimulatedGroupService,
    evaluate_group_step,
)
from onboarding import NewHireRequest

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


class _NoDuplicateAddService(SimulatedGroupService):
    """Raises if add_member is called for a group the employee is already
    in — enforces that the step actually skips already-member groups
    instead of just happening to end up with the right state."""

    def add_member(self, employee_id, group):
        if self.is_member(employee_id, group):
            raise AssertionError(f"add_member called for already-member group {group!r}")
        super().add_member(employee_id, group)


class _FailsToAddService:
    """Stub simulating a backend where add_member silently fails to take
    effect for specific groups, to check the step verifies rather than
    trusting the add call, and doesn't roll back groups that did verify."""

    def __init__(self, fail_groups):
        self._fail_groups = set(fail_groups)
        self._members: set[str] = set()

    def is_member(self, employee_id, group):
        return group in self._members

    def add_member(self, employee_id, group):
        if group not in self._fail_groups:
            self._members.add(group)


# --- assigning missing groups -------------------------------------------

def test_assigns_all_required_groups_when_none_are_member():
    service = SimulatedGroupService()
    request = make_request(employee_id="EMP-3001")

    result = evaluate_group_step(request, MAPPING, service)

    assert result.status == GROUP_STATUS_VERIFIED
    assert result.verified is True
    assert result.required_groups == ["Finance-Users", "Finance-Shared"]
    assert result.verified_groups == frozenset({"Finance-Users", "Finance-Shared"})
    assert result.failed_groups == frozenset()
    assert result.manual_review_reason is None
    assert service.is_member("EMP-3001", "Finance-Users")
    assert service.is_member("EMP-3001", "Finance-Shared")


# --- leaving existing membership alone -----------------------------------

def test_leaves_already_member_group_alone():
    service = _NoDuplicateAddService()
    service.seed_membership("EMP-3001", ["Finance-Users"])
    request = make_request(employee_id="EMP-3001")

    result = evaluate_group_step(request, MAPPING, service)  # would raise if re-added

    assert result.status == GROUP_STATUS_VERIFIED
    assert result.verified_groups == frozenset({"Finance-Users", "Finance-Shared"})


def test_rerun_after_first_call_does_not_reassign_or_fail():
    service = _NoDuplicateAddService()
    request = make_request(employee_id="EMP-3001")

    first = evaluate_group_step(request, MAPPING, service)
    second = evaluate_group_step(request, MAPPING, service)  # would raise if re-added

    assert first.status == GROUP_STATUS_VERIFIED
    assert second.status == GROUP_STATUS_VERIFIED
    assert second.verified_groups == frozenset({"Finance-Users", "Finance-Shared"})


# --- unmapped department/role --------------------------------------------

def test_no_mapping_routes_to_manual_review():
    service = SimulatedGroupService()
    request = make_request(department="Research", job_role="Research Analyst")

    result = evaluate_group_step(request, MAPPING, service)

    assert result.status == GROUP_STATUS_MANUAL_REVIEW
    assert result.verified is False
    assert result.required_groups is None
    assert "Research" in result.manual_review_reason
    assert "Research Analyst" in result.manual_review_reason


# --- verification failure, no rollback ------------------------------------

def test_failed_verification_for_one_group_does_not_roll_back_others():
    service = _FailsToAddService(fail_groups={"Finance-Shared"})
    request = make_request(employee_id="EMP-3001")

    result = evaluate_group_step(request, MAPPING, service)

    assert result.status == GROUP_STATUS_FAILED
    assert result.verified is False
    assert result.verified_groups == frozenset({"Finance-Users"})
    assert result.failed_groups == frozenset({"Finance-Shared"})
    assert "Finance-Shared" in result.manual_review_reason

    # Not rolled back.
    assert service.is_member("EMP-3001", "Finance-Users")


# --- the simulated service itself -----------------------------------------

def test_is_member_false_when_none_assigned():
    service = SimulatedGroupService()
    assert service.is_member("EMP-3001", "Finance-Users") is False


def test_seed_membership_sets_exact_state():
    service = SimulatedGroupService()
    service.seed_membership("EMP-3001", ["Finance-Users"])
    assert service.is_member("EMP-3001", "Finance-Users") is True
    assert service.is_member("EMP-3001", "Finance-Shared") is False
