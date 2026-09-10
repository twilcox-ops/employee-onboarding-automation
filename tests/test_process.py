"""Stage 10 tests: the top-level process_onboarding entry point. These
only prove the existing pieces (rerun/workflow/audit, notifications) are
wired together correctly — no new business logic is exercised or
duplicated here. No real Graph, email delivery, corporate-card
integration, or Docker is exercised.
"""

import datetime

from entra import SimulatedEntraService
from groups import SimulatedGroupService
from licensing import SimulatedLicensingService
from notifications import (
    OUTCOME_IT_NOTIFIED_INCOMPLETE,
    OUTCOME_IT_NOTIFIED_MANAGER_UNRESOLVED,
    OUTCOME_MANAGER_NOTIFIED,
    SimulatedNotifier,
)
from onboarding import NewHireRequest, RequestDecision
from process import process_onboarding

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
        manager="Dana Mitchell",
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
    return SimulatedEntraService(), SimulatedLicensingService(), SimulatedGroupService()


# --- successful onboarding ---------------------------------------------------

def test_successful_onboarding_completes_audits_and_notifies_manager(tmp_path):
    audit_path = tmp_path / "audit_log.jsonl"
    entra, license_, groups = new_services()
    entra.seed_account("dana.mitchell@company.example", "EMP-MGR1", "Dana", "Mitchell")
    notifier = SimulatedNotifier()
    request = make_request()
    decision = make_decision()

    result = process_onboarding(
        request, decision, entra, license_, groups, notifier, MAPPING, T1, audit_log_path=audit_path,
    )

    assert result.request_id == "ONB-TEST"
    assert result.employee_id == "EMP-TEST"
    assert result.onboarding_result.completed is True
    assert result.audit_record["attempt_type"] == "initial"
    assert result.audit_record["overall_result"] == "completed"
    assert result.notification_outcome.outcome == OUTCOME_MANAGER_NOTIFIED
    assert result.notification_outcome.onboarding_completed is True
    assert result.notification_outcome.needs_manual_review is False
    assert notifier.sent == [result.notification_outcome.notification]


# --- incomplete onboarding ----------------------------------------------------

def test_incomplete_onboarding_is_audited_and_it_is_notified(tmp_path):
    audit_path = tmp_path / "audit_log.jsonl"
    entra, license_, groups = new_services()
    entra.seed_account("dana.mitchell@company.example", "EMP-MGR1", "Dana", "Mitchell")
    notifier = SimulatedNotifier()
    request = make_request(corporate_card_required="Yes")  # blocks completion
    decision = make_decision(card_action="setup_required")

    result = process_onboarding(
        request, decision, entra, license_, groups, notifier, MAPPING, T1, audit_log_path=audit_path,
    )

    assert result.onboarding_result.completed is False
    assert result.audit_record["overall_result"] == "incomplete_failed"
    assert result.audit_record["failed_steps"] == [
        {
            "step": "corporate_card", "status": "pending_integration",
            "verified": False, "reason": result.onboarding_result.card_note,
        }
    ]
    assert result.notification_outcome.outcome == OUTCOME_IT_NOTIFIED_INCOMPLETE
    assert result.notification_outcome.manager_lookup is None  # never attempted


# --- rerun ---------------------------------------------------------------

def test_rerun_preserves_initial_then_rerun_and_continues_to_notify(tmp_path):
    audit_path = tmp_path / "audit_log.jsonl"
    entra, license_, groups = new_services()
    entra.seed_account("dana.mitchell@company.example", "EMP-MGR1", "Dana", "Mitchell")
    notifier = SimulatedNotifier()
    request = make_request()
    decision = make_decision()

    first = process_onboarding(
        request, decision, entra, license_, groups, notifier, MAPPING, T1, audit_log_path=audit_path,
    )
    second = process_onboarding(
        request, decision, entra, license_, groups, notifier, MAPPING, T2, audit_log_path=audit_path,
    )

    assert first.audit_record["attempt_type"] == "initial"
    assert second.audit_record["attempt_type"] == "rerun"
    # Nothing re-created/re-assigned on the rerun.
    assert second.onboarding_result.entra.status == "already_exists"
    assert second.onboarding_result.license.status == "already_assigned"
    assert second.onboarding_result.completed is True
    assert len(notifier.sent) == 2  # one notification per attempt, neither suppressed


def test_rerun_of_incomplete_case_continues_independent_steps_without_rollback(tmp_path):
    # Unmapped role fails the group step; Entra/license are independent
    # and should stay verified across both attempts, unrolled-back.
    audit_path = tmp_path / "audit_log.jsonl"
    entra, license_, groups = new_services()
    notifier = SimulatedNotifier()
    request = make_request(department="Research", job_role="Research Analyst")
    decision = make_decision(required_groups=None)

    process_onboarding(
        request, decision, entra, license_, groups, notifier, MAPPING, T1, audit_log_path=audit_path,
    )
    second = process_onboarding(
        request, decision, entra, license_, groups, notifier, MAPPING, T2, audit_log_path=audit_path,
    )

    assert second.audit_record["attempt_type"] == "rerun"
    assert second.onboarding_result.groups.verified is False
    assert second.onboarding_result.entra.verified is True
    assert second.onboarding_result.license.verified is True
    assert second.onboarding_result.completed is False


# --- completed onboarding, manager lookup unresolved -------------------------

def test_completed_onboarding_with_unresolved_manager_needs_review(tmp_path):
    audit_path = tmp_path / "audit_log.jsonl"
    entra, license_, groups = new_services()  # no manager account seeded
    notifier = SimulatedNotifier()
    request = make_request(manager="Nobody Here")
    decision = make_decision()

    result = process_onboarding(
        request, decision, entra, license_, groups, notifier, MAPPING, T1, audit_log_path=audit_path,
    )

    # Onboarding itself completed — the audit record shows that plainly...
    assert result.onboarding_result.completed is True
    assert result.audit_record["overall_result"] == "completed"
    # ...while the notification layer separately flags manual review for
    # the unresolved manager, without the two being conflated.
    assert result.notification_outcome.onboarding_completed is True
    assert result.notification_outcome.outcome == OUTCOME_IT_NOTIFIED_MANAGER_UNRESOLVED
    assert result.notification_outcome.needs_manual_review is True
    assert notifier.sent[0].recipient == "IT"
