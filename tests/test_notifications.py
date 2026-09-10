"""Stage 8 tests: IT manual-review alerts and the manager completion
email, both simulated/local. No real email, Graph, audit, or rerun
execution logic is exercised here.
"""

import datetime

from entra import SimulatedEntraService
from groups import SimulatedGroupService
from licensing import SimulatedLicensingService
from notifications import (
    MANAGER_LOOKUP_AMBIGUOUS,
    MANAGER_LOOKUP_NOT_FOUND,
    NOTIFICATION_IT_REVIEW,
    NOTIFICATION_MANAGER_COMPLETE,
    OUTCOME_IT_NOTIFIED_INCOMPLETE,
    OUTCOME_IT_NOTIFIED_MANAGER_UNRESOLVED,
    OUTCOME_MANAGER_NOTIFIED,
    SimulatedNotifier,
    lookup_manager,
    notify_for_attempt,
)
from onboarding import NewHireRequest
from workflow import run_onboarding

MAPPING = {("Finance", "Financial Analyst"): ["Finance-Users", "Finance-Shared"]}


def make_request(**overrides):
    defaults = dict(
        request_id="ONB-TEST",
        employee_id="EMP-TEST",
        first_name="Test",
        last_name="Person",
        department="Finance",
        job_role="Financial Analyst",
        manager="Dana Mitchell",
        employment_type="Regular Full-Time",
        start_date=datetime.date(2026, 9, 17),
        corporate_card_required="No",
        ready_for_onboarding="Yes",
        hr_ready_date=datetime.date(2026, 9, 11),
    )
    defaults.update(overrides)
    return NewHireRequest(**defaults)


def run_workflow(request, entra_service):
    return run_onboarding(
        request, entra_service, SimulatedLicensingService(), SimulatedGroupService(), MAPPING,
    )


# --- completion path: manager uniquely found ---------------------------------

def test_completed_onboarding_with_unique_manager_sends_only_manager_email():
    entra_service = SimulatedEntraService()
    entra_service.seed_account(
        "dana.mitchell@company.example", "EMP-MGR1", "Dana", "Mitchell",
    )
    request = make_request()
    result = run_workflow(request, entra_service)
    notifier = SimulatedNotifier()

    outcome = notify_for_attempt(request, result, entra_service, notifier)

    assert outcome.onboarding_completed is True
    assert outcome.outcome == OUTCOME_MANAGER_NOTIFIED
    assert outcome.needs_manual_review is False
    assert outcome.manager_lookup.account.employee_id == "EMP-MGR1"

    assert len(notifier.sent) == 1
    sent = notifier.sent[0]
    assert sent.kind == NOTIFICATION_MANAGER_COMPLETE
    assert sent.recipient == "dana.mitchell@company.example"


# --- incomplete onboarding: IT only, no manager lookup attempted -------------

def test_incomplete_onboarding_sends_it_notification_and_skips_manager_lookup():
    entra_service = SimulatedEntraService()
    entra_service.seed_account("dana.mitchell@company.example", "EMP-MGR1", "Dana", "Mitchell")
    request = make_request(corporate_card_required="Yes")  # blocks completion
    result = run_workflow(request, entra_service)
    notifier = SimulatedNotifier()

    outcome = notify_for_attempt(request, result, entra_service, notifier)

    assert outcome.onboarding_completed is False
    assert outcome.outcome == OUTCOME_IT_NOTIFIED_INCOMPLETE
    assert outcome.needs_manual_review is False  # incomplete, not ambiguous
    assert outcome.manager_lookup is None

    assert len(notifier.sent) == 1
    sent = notifier.sent[0]
    assert sent.kind == NOTIFICATION_IT_REVIEW
    assert sent.recipient == "IT"
    assert "Verified: entra_account, license, groups" in sent.summary
    assert "corporate_card" in sent.summary


def test_rerun_of_still_incomplete_attempt_sends_its_own_it_notification():
    entra_service = SimulatedEntraService()
    request = make_request(corporate_card_required="Yes")
    notifier = SimulatedNotifier()

    for _ in range(2):  # simulates an initial attempt, then a rerun
        result = run_workflow(request, entra_service)
        notify_for_attempt(request, result, entra_service, notifier)

    assert len(notifier.sent) == 2
    assert all(n.kind == NOTIFICATION_IT_REVIEW for n in notifier.sent)


# --- completed onboarding, manager can't be uniquely identified --------------

def test_manager_not_found_preserves_onboarding_completed_but_needs_review():
    entra_service = SimulatedEntraService()  # no manager account seeded at all
    request = make_request(manager="Nobody Here")
    result = run_workflow(request, entra_service)
    notifier = SimulatedNotifier()

    outcome = notify_for_attempt(request, result, entra_service, notifier)

    assert outcome.onboarding_completed is True  # preserved, not folded into "failed"
    assert outcome.outcome == OUTCOME_IT_NOTIFIED_MANAGER_UNRESOLVED
    assert outcome.needs_manual_review is True
    assert outcome.manager_lookup.status == MANAGER_LOOKUP_NOT_FOUND

    sent = notifier.sent[0]
    assert sent.kind == NOTIFICATION_IT_REVIEW
    assert sent.recipient == "IT"
    assert "completed and verified" in sent.summary


def test_multiple_manager_matches_routes_to_manual_review():
    entra_service = SimulatedEntraService()
    entra_service.seed_account("dana.mitchell@company.example", "EMP-MGR1", "Dana", "Mitchell")
    entra_service.seed_account("dana.mitchell2@company.example", "EMP-MGR2", "Dana", "Mitchell")
    request = make_request()
    result = run_workflow(request, entra_service)
    notifier = SimulatedNotifier()

    outcome = notify_for_attempt(request, result, entra_service, notifier)

    assert outcome.onboarding_completed is True
    assert outcome.outcome == OUTCOME_IT_NOTIFIED_MANAGER_UNRESOLVED
    assert outcome.needs_manual_review is True
    assert outcome.manager_lookup.status == MANAGER_LOOKUP_AMBIGUOUS
    assert "2 Entra accounts found" in outcome.manager_lookup.reason


# --- lookup_manager / SimulatedEntraService.find_by_display_name directly ---

def test_lookup_manager_found_not_found_and_ambiguous():
    entra_service = SimulatedEntraService()
    assert lookup_manager("Dana Mitchell", entra_service).status == MANAGER_LOOKUP_NOT_FOUND

    entra_service.seed_account("dana.mitchell@company.example", "EMP-MGR1", "Dana", "Mitchell")
    assert lookup_manager("Dana Mitchell", entra_service).status == "found"

    entra_service.seed_account("dana.mitchell2@company.example", "EMP-MGR2", "Dana", "Mitchell")
    assert lookup_manager("Dana Mitchell", entra_service).status == MANAGER_LOOKUP_AMBIGUOUS
