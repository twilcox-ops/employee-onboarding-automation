"""Stage 9 tests: rerun behavior. Each existing step function already
re-verifies live simulated state rather than trusting the audit log (see
rerun.py's module docstring) — these tests exercise that directly by
calling run_onboarding_attempt twice against the *same* service instances
and checking the second call's outcome. The audit log is only ever
checked for the initial/rerun flag; none of these tests encode a policy
that compares current state against what an earlier attempt recorded —
only the current live state decides what's true, and manual review is
used only for genuine identity/desired-state/mapping ambiguity, not
merely because something differs from before. No real Graph, email
delivery, or Docker is exercised here.
"""

import datetime

from entra import SimulatedEntraService
from groups import SimulatedGroupService
from licensing import E3_SKU, SimulatedLicensingService
from onboarding import NewHireRequest, RequestDecision
from rerun import has_prior_attempt, run_onboarding_attempt

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


class _FailsFirstAssignService:
    """Wraps a real SimulatedLicensingService; the first assign_license
    call silently fails (a transient issue), every call after that works
    normally — so a retry of the same known desired license succeeds once
    the issue has cleared, with no change needed to evaluate_license_step
    itself."""

    def __init__(self, inner):
        self._inner = inner
        self._failed_once = False

    def get_licenses(self, employee_id):
        return self._inner.get_licenses(employee_id)

    def assign_license(self, employee_id, sku):
        if not self._failed_once:
            self._failed_once = True
            return
        self._inner.assign_license(employee_id, sku)


def run_attempt(request, decision, entra, license_, groups, timestamp, audit_path):
    return run_onboarding_attempt(
        request, decision, entra, license_, groups, MAPPING, timestamp, audit_log_path=audit_path,
    )


# --- initial vs rerun --------------------------------------------------------

def test_has_prior_attempt_false_then_true_after_one_record(tmp_path):
    audit_path = tmp_path / "audit_log.jsonl"
    request = make_request()

    assert has_prior_attempt(request, audit_path) is False

    run_attempt(
        request, make_decision(), SimulatedEntraService(), SimulatedLicensingService(),
        SimulatedGroupService(), T1, audit_path,
    )

    assert has_prior_attempt(request, audit_path) is True


# --- current state already correct: verified, left unchanged ---------------

def test_current_state_already_correct_stays_verified_and_unchanged(tmp_path):
    audit_path = tmp_path / "audit_log.jsonl"
    request = make_request()
    decision = make_decision()
    entra, license_, groups = SimulatedEntraService(), SimulatedLicensingService(), SimulatedGroupService()

    result1, record1 = run_attempt(request, decision, entra, license_, groups, T1, audit_path)
    assert result1.completed is True
    assert record1["attempt_type"] == "initial"

    result2, record2 = run_attempt(request, decision, entra, license_, groups, T2, audit_path)

    assert record2["attempt_type"] == "rerun"
    assert record2["overall_result"] == "completed"  # automated, no manual review
    assert result2.completed is True
    assert result2.entra.status == "already_exists"      # not recreated
    assert result2.license.status == "already_assigned"  # not reassigned
    assert result2.groups.status == "verified"
    assert license_.get_licenses("EMP-TEST") == frozenset({E3_SKU})  # unchanged


# --- current state already correct, even though an earlier attempt failed --

def test_current_state_already_correct_even_after_an_earlier_failure(tmp_path):
    audit_path = tmp_path / "audit_log.jsonl"
    request = make_request()
    decision = make_decision()
    entra, groups = SimulatedEntraService(), SimulatedGroupService()
    license_ = SimulatedLicensingService()
    license_.seed_licenses("EMP-TEST", ["Project Plan 3"])  # nonstandard, no E3

    result1, record1 = run_attempt(request, decision, entra, license_, groups, T1, audit_path)
    assert result1.license.verified is False
    assert record1["overall_result"] == "manual_review"

    # The live license state is now correct — it doesn't matter how it got
    # that way; the rerun only ever looks at what's true now.
    license_.seed_licenses("EMP-TEST", [E3_SKU])

    result2, record2 = run_attempt(request, decision, entra, license_, groups, T2, audit_path)

    assert record2["attempt_type"] == "rerun"
    assert record2["overall_result"] == "completed"  # automated, no manual review
    assert result2.license.status == "already_assigned"
    assert result2.license.verified is True
    assert result2.completed is True


# --- desired state known, live state wrong/missing: fix it, then verify -----

def test_known_desired_state_gets_fixed_and_verified_on_rerun(tmp_path):
    audit_path = tmp_path / "audit_log.jsonl"
    request = make_request()
    decision = make_decision()
    entra, groups = SimulatedEntraService(), SimulatedGroupService()
    real_license = SimulatedLicensingService()
    license_ = _FailsFirstAssignService(real_license)

    result1, record1 = run_attempt(request, decision, entra, license_, groups, T1, audit_path)
    assert result1.license.verified is False
    assert "did not verify" in result1.license.manual_review_reason

    result2, record2 = run_attempt(request, decision, entra, license_, groups, T2, audit_path)

    assert record2["attempt_type"] == "rerun"
    assert record2["overall_result"] == "completed"  # automated, no manual review
    assert result2.license.status == "assigned"
    assert result2.license.verified is True
    assert real_license.get_licenses("EMP-TEST") == frozenset({E3_SKU})


# --- identity ambiguous (UPN collision): manual review, not repaired --------

def test_identity_ambiguous_upn_collision_routes_to_manual_review(tmp_path):
    # This is about what the live directory shows right now — a UPN
    # currently held by a different employee — not about comparing
    # against what an earlier attempt achieved. The two-call shape here
    # only proves that a rerun makes this same live-state decision
    # correctly, the same as an initial attempt would.
    audit_path = tmp_path / "audit_log.jsonl"
    request = make_request(first_name="Alex", last_name="Carter")
    decision = make_decision()
    entra = SimulatedEntraService()
    license_, groups = SimulatedLicensingService(), SimulatedGroupService()

    result1, record1 = run_attempt(request, decision, entra, license_, groups, T1, audit_path)
    assert result1.entra.status == "created"
    assert result1.completed is True

    # The live directory now shows this UPN held by a different employee.
    entra.seed_account("alex.carter@company.example", "EMP-SOMEONE-ELSE", "Alex", "Carter")

    result2, record2 = run_attempt(request, decision, entra, license_, groups, T2, audit_path)

    assert record2["attempt_type"] == "rerun"
    assert result2.entra.status == "manual_review"
    assert "EMP-SOMEONE-ELSE" in result2.entra.manual_review_reason
    assert record2["overall_result"] == "manual_review"

    # Independent steps that already succeeded are not rolled back.
    assert result2.license.verified is True
    assert result2.groups.verified is True


# --- mapping unknown (unmapped role): manual review, stays that way ---------

def test_unmapped_role_stays_manual_review_across_reruns(tmp_path):
    audit_path = tmp_path / "audit_log.jsonl"
    request = make_request(department="Research", job_role="Research Analyst")
    decision = make_decision(required_groups=None)
    entra, license_, groups = SimulatedEntraService(), SimulatedLicensingService(), SimulatedGroupService()

    result1, _ = run_attempt(request, decision, entra, license_, groups, T1, audit_path)
    result2, record2 = run_attempt(request, decision, entra, license_, groups, T2, audit_path)

    assert result1.groups.status == "manual_review"
    assert result2.groups.status == "manual_review"
    assert record2["attempt_type"] == "rerun"
    assert record2["overall_result"] == "manual_review"
