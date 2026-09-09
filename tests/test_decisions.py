"""Stage 2 tests: business decisions made from already-loaded data — scope,
scheduling, group mapping, corporate-card validation, and duplicate
handling. Nothing here touches Entra, notifications, or audit logging.
"""

import datetime
from pathlib import Path

from onboarding import (
    RequestDecision,
    compute_onboarding_date,
    corporate_card_decision,
    evaluate_all,
    find_duplicate_request_ids,
    is_in_scope,
    is_late,
    load_group_mapping,
    load_holidays,
    load_new_hires,
    lookup_groups,
)

CORPUS_DIR = Path(__file__).parent.parent / "synthetic_corpus"


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
    # Import here to avoid a hard dependency on dataclass field order.
    from onboarding import NewHireRequest
    return NewHireRequest(**defaults)


# --- scope ------------------------------------------------------------

def test_regular_full_time_is_in_scope():
    assert is_in_scope(make_request(employment_type="Regular Full-Time")) is True


def test_excluded_employment_types_are_out_of_scope():
    for employment_type in ["Contractor", "Intern", "Temporary Worker", "Rehire"]:
        assert is_in_scope(make_request(employment_type=employment_type)) is False


# --- scheduling ---------------------------------------------------------

HOLIDAYS = {
    datetime.date(2026, 9, 7),    # Labor Day
    datetime.date(2026, 11, 26),
    datetime.date(2026, 11, 27),
}


def test_onboarding_date_monday_start_is_previous_friday():
    # REQUIREMENTS.md example: Monday start date -> onboard the previous Friday.
    monday = datetime.date(2026, 9, 21)
    assert compute_onboarding_date(monday, HOLIDAYS) == datetime.date(2026, 9, 18)


def test_onboarding_date_skips_a_monday_holiday():
    # REQUIREMENTS.md example: Tuesday start with Monday as a holiday ->
    # onboard the previous Friday. 2026-09-08 is a Tuesday; 2026-09-07 (Mon)
    # is Labor Day.
    tuesday = datetime.date(2026, 9, 8)
    assert compute_onboarding_date(tuesday, HOLIDAYS) == datetime.date(2026, 9, 4)


def test_is_late_when_hr_ready_after_onboarding_date():
    onboarding_date = datetime.date(2026, 12, 18)
    assert is_late(datetime.date(2026, 12, 21), onboarding_date) is True  # ONB-1032 case


def test_is_late_false_on_or_before_onboarding_date():
    onboarding_date = datetime.date(2026, 12, 18)
    assert is_late(datetime.date(2026, 12, 18), onboarding_date) is False
    assert is_late(datetime.date(2026, 12, 15), onboarding_date) is False


# --- duplicates -----------------------------------------------------------

def test_find_duplicate_request_ids_flags_only_the_later_request():
    first = make_request(request_id="ONB-1", employee_id="EMP-1",
                          start_date=datetime.date(2026, 9, 28))
    second = make_request(request_id="ONB-2", employee_id="EMP-1",
                           start_date=datetime.date(2026, 9, 28))
    unrelated = make_request(request_id="ONB-3", employee_id="EMP-2",
                              start_date=datetime.date(2026, 9, 28))

    duplicates = find_duplicate_request_ids([first, second, unrelated])

    assert duplicates == {"ONB-2"}


def test_find_duplicate_request_ids_ignores_rows_with_no_start_date():
    a = make_request(request_id="ONB-1", employee_id="EMP-1", start_date=None)
    b = make_request(request_id="ONB-2", employee_id="EMP-1", start_date=None)
    assert find_duplicate_request_ids([a, b]) == set()


# --- group mapping --------------------------------------------------------

def test_lookup_groups_returns_mapped_groups():
    mapping = {("Finance", "Financial Analyst"): ["Finance-Users", "Finance-Shared"]}
    assert lookup_groups("Finance", "Financial Analyst", mapping) == ["Finance-Users", "Finance-Shared"]


def test_lookup_groups_returns_none_for_unmapped_combination():
    mapping = {("Finance", "Financial Analyst"): ["Finance-Users"]}
    assert lookup_groups("Research", "Research Analyst", mapping) is None


# --- corporate card ---------------------------------------------------------

def test_corporate_card_decision_yes_and_no():
    assert corporate_card_decision("Yes") == "setup_required"
    assert corporate_card_decision("No") == "no_action"


def test_corporate_card_decision_flags_missing_or_invalid():
    for value in ["", "Pending", "Y", "yes", "NO"]:
        assert corporate_card_decision(value) == "manual_review"


# --- combined evaluation, against the real corpus --------------------------

def test_evaluate_all_against_corpus_matches_known_edge_cases():
    hires, _ = load_new_hires(CORPUS_DIR / "new_hires.csv")
    mapping, _ = load_group_mapping(CORPUS_DIR / "group_mapping.xlsx")
    holidays, _ = load_holidays(CORPUS_DIR / "company_holidays.csv")

    decisions = {d.request_id: d for d in evaluate_all(hires, mapping, holidays)}

    # Known duplicate pairs: the later Request ID is flagged, the earlier is not.
    assert decisions["ONB-1004"].is_duplicate is False
    assert decisions["ONB-1028"].is_duplicate is True
    assert decisions["ONB-1023"].is_duplicate is False
    assert decisions["ONB-1068"].is_duplicate is True
    assert decisions["ONB-1041"].is_duplicate is False
    assert decisions["ONB-1096"].is_duplicate is True

    # Known unmapped department/role combinations.
    assert decisions["ONB-1012"].required_groups is None   # Research / Research Analyst
    assert decisions["ONB-1037"].required_groups is None   # Finance / Finance Systems Specialist
    assert decisions["ONB-1082"].required_groups is None   # Legal / Paralegal

    # Known invalid/missing corporate card values.
    assert decisions["ONB-1015"].card_action == "manual_review"  # blank
    assert decisions["ONB-1053"].card_action == "manual_review"  # "Pending"
    assert decisions["ONB-1089"].card_action == "manual_review"  # "Y"

    # Known out-of-scope employment types.
    assert decisions["ONB-1008"].in_scope is False  # Contractor
    assert decisions["ONB-1019"].in_scope is False  # Intern
    assert decisions["ONB-1045"].in_scope is False  # Temporary Worker
    assert decisions["ONB-1074"].in_scope is False  # Rehire

    # Known late-readiness cases identified during requirements discovery.
    assert decisions["ONB-1032"].is_late is True
    assert decisions["ONB-1062"].is_late is True

    # A routine, fully clean row should need no manual review.
    assert decisions["ONB-1001"].needs_manual_review is False


def test_request_decision_needs_manual_review_reflects_reasons():
    clean = RequestDecision("ONB-1", "EMP-1", True, False, None, False, ["G1"], "no_action", [])
    assert clean.needs_manual_review is False

    flagged = RequestDecision("ONB-2", "EMP-2", True, False, None, False, None, "manual_review",
                               ["no group mapping for department 'X' / job role 'Y'"])
    assert flagged.needs_manual_review is True
