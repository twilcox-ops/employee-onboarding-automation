"""Stage 1 tests: loading and validating the three synthetic_corpus/ files.

These only check that files are read correctly and that structural problems
(bad dates, missing columns) are surfaced rather than dropped or guessed at.
No business decisions (scope, duplicates, unmapped groups, card validity,
etc.) are tested here — those belong to later stages.
"""

import datetime
from pathlib import Path

import pytest

from onboarding import load_group_mapping, load_holidays, load_new_hires

CORPUS_DIR = Path(__file__).parent.parent / "synthetic_corpus"


def test_load_new_hires_reads_all_rows():
    hires, issues = load_new_hires(CORPUS_DIR / "new_hires.csv")
    assert len(hires) == 100
    assert issues == []  # the known corpus has no unparseable dates


def test_load_new_hires_parses_dates():
    hires, _ = load_new_hires(CORPUS_DIR / "new_hires.csv")
    first = hires[0]
    assert first.request_id == "ONB-1001"
    assert first.employee_id == "EMP-3001"
    assert first.start_date == datetime.date(2026, 9, 17)
    assert first.hr_ready_date == datetime.date(2026, 9, 11)


def test_load_new_hires_keeps_corporate_card_value_raw():
    # Interpreting these values (blank/"Pending"/"Y") is a later stage's job;
    # Stage 1 just has to load them faithfully.
    hires, _ = load_new_hires(CORPUS_DIR / "new_hires.csv")
    by_id = {h.request_id: h for h in hires}
    assert by_id["ONB-1015"].corporate_card_required == ""
    assert by_id["ONB-1053"].corporate_card_required == "Pending"
    assert by_id["ONB-1089"].corporate_card_required == "Y"


def test_load_new_hires_missing_column_raises(tmp_path):
    bad_csv = tmp_path / "bad.csv"
    bad_csv.write_text("Request ID,Employee ID\nONB-1,EMP-1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_new_hires(bad_csv)


def test_load_new_hires_unparseable_date_is_reported_not_dropped(tmp_path):
    header = (
        "Request ID,Employee ID,First Name,Last Name,Department,Job Role,"
        "Manager,Employment Type,Start Date,Corporate Card Required,"
        "Ready for Onboarding,HR Ready Date\n"
    )
    row = (
        "ONB-9999,EMP-9999,Test,Person,Finance,Financial Analyst,"
        "Some Manager,Regular Full-Time,not-a-date,No,Yes,2026-09-01\n"
    )
    bad_csv = tmp_path / "bad.csv"
    bad_csv.write_text(header + row, encoding="utf-8")

    hires, issues = load_new_hires(bad_csv)
    assert len(hires) == 1
    assert hires[0].start_date is None
    assert len(issues) == 1
    assert issues[0].field == "Start Date"


def test_load_group_mapping_splits_groups():
    mapping, issues = load_group_mapping(CORPUS_DIR / "group_mapping.xlsx")
    assert issues == []
    assert mapping[("Finance", "Financial Analyst")] == ["Finance-Users", "Finance-Shared"]
    assert mapping[("Information Technology", "Systems Administrator")] == [
        "InformationTechnology-Users", "IT-Users", "IT-Server-Operators",
    ]
    # Combinations absent from the mapping (e.g. Research/Legal in the
    # corpus) are simply not keys here — deciding what that means (manual
    # review) is a later stage, not this loader.
    assert ("Research", "Research Analyst") not in mapping


def test_load_holidays_reads_all_dates():
    holidays, issues = load_holidays(CORPUS_DIR / "company_holidays.csv")
    assert issues == []
    assert len(holidays) == 6
    assert datetime.date(2026, 11, 26) in holidays  # Thanksgiving
