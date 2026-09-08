"""Employee Onboarding Automation.

Stage 1: load and validate the three synthetic_corpus/ input files.

Only structural loading lives here — parsing each file into a usable shape
and reporting rows/fields that can't be read as given (e.g. an unparseable
date, a blank required cell). Business decisions such as scope filtering,
duplicate detection, unmapped-group handling, or corporate-card validity
are NOT made here — those are later stages, per REQUIREMENTS.md.
"""

from __future__ import annotations

import csv
import datetime
from dataclasses import dataclass
from pathlib import Path

import openpyxl

DATE_FORMAT = "%Y-%m-%d"

NEW_HIRE_COLUMNS = [
    "Request ID", "Employee ID", "First Name", "Last Name", "Department",
    "Job Role", "Manager", "Employment Type", "Start Date",
    "Corporate Card Required", "Ready for Onboarding", "HR Ready Date",
]

GROUP_MAPPING_COLUMNS = ["Department", "Job Role", "Required Groups"]

HOLIDAY_COLUMNS = ["Date", "Holiday Name"]


@dataclass
class NewHireRequest:
    """One row of new_hires.csv, with dates parsed and everything else kept
    as the raw text HR supplied. Interpreting a value (e.g. deciding that a
    blank Corporate Card Required means manual review) happens in a later
    stage, not here.
    """
    request_id: str
    employee_id: str
    first_name: str
    last_name: str
    department: str
    job_role: str
    manager: str
    employment_type: str
    start_date: datetime.date | None
    corporate_card_required: str
    ready_for_onboarding: str
    hr_ready_date: datetime.date | None


@dataclass
class LoadIssue:
    """A structural problem found while loading a file — the row is still
    returned to the caller; this just flags that one field in it couldn't
    be read as expected.
    """
    source: str
    identifier: str
    field: str
    problem: str


def _parse_date(value: str) -> datetime.date | None:
    value = value.strip()
    if not value:
        return None
    try:
        return datetime.datetime.strptime(value, DATE_FORMAT).date()
    except ValueError:
        return None


def load_new_hires(path: str | Path) -> tuple[list[NewHireRequest], list[LoadIssue]]:
    """Load new_hires.csv into NewHireRequest records.

    Returns (records, issues). Every row is returned as a record even if a
    date couldn't be parsed (the field is left as None); the bad value is
    surfaced as an issue instead of being silently dropped or guessed at.
    """
    path = Path(path)
    records: list[NewHireRequest] = []
    issues: list[LoadIssue] = []

    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing = [c for c in NEW_HIRE_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"{path.name} is missing expected column(s): {missing}")

        for row_num, row in enumerate(reader, start=2):  # row 1 is the header
            identifier = row["Request ID"].strip() or f"row {row_num}"

            start_date = _parse_date(row["Start Date"])
            if row["Start Date"].strip() and start_date is None:
                issues.append(LoadIssue("new_hires.csv", identifier, "Start Date",
                                         f"unparseable date: {row['Start Date']!r}"))

            hr_ready_date = _parse_date(row["HR Ready Date"])
            if row["HR Ready Date"].strip() and hr_ready_date is None:
                issues.append(LoadIssue("new_hires.csv", identifier, "HR Ready Date",
                                         f"unparseable date: {row['HR Ready Date']!r}"))

            records.append(NewHireRequest(
                request_id=row["Request ID"].strip(),
                employee_id=row["Employee ID"].strip(),
                first_name=row["First Name"].strip(),
                last_name=row["Last Name"].strip(),
                department=row["Department"].strip(),
                job_role=row["Job Role"].strip(),
                manager=row["Manager"].strip(),
                employment_type=row["Employment Type"].strip(),
                start_date=start_date,
                corporate_card_required=row["Corporate Card Required"].strip(),
                ready_for_onboarding=row["Ready for Onboarding"].strip(),
                hr_ready_date=hr_ready_date,
            ))

    return records, issues


def load_group_mapping(
    path: str | Path,
) -> tuple[dict[tuple[str, str], list[str]], list[LoadIssue]]:
    """Load group_mapping.xlsx into a {(department, job role): [groups]} lookup.

    The "Required Groups" cell packs multiple group names separated by ";" —
    that is split here since it's purely a matter of reading the file, not a
    business decision. Whether a given department/role combination is
    present or absent is left for the caller to interpret later.
    """
    path = Path(path)
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    sheet = wb.active

    rows = sheet.iter_rows(values_only=True)
    header = [str(h).strip() if h is not None else "" for h in next(rows)]
    missing = [c for c in GROUP_MAPPING_COLUMNS if c not in header]
    if missing:
        raise ValueError(f"{path.name} is missing expected column(s): {missing}")
    col = {name: header.index(name) for name in GROUP_MAPPING_COLUMNS}

    mapping: dict[tuple[str, str], list[str]] = {}
    issues: list[LoadIssue] = []

    for row_num, row in enumerate(rows, start=2):
        department = str(row[col["Department"]] or "").strip()
        job_role = str(row[col["Job Role"]] or "").strip()
        groups_cell = str(row[col["Required Groups"]] or "").strip()

        if not department or not job_role or not groups_cell:
            issues.append(LoadIssue("group_mapping.xlsx", f"row {row_num}",
                                     "Department/Job Role/Required Groups",
                                     "one or more required fields are blank"))
            continue

        groups = [g.strip() for g in groups_cell.split(";") if g.strip()]
        mapping[(department, job_role)] = groups

    wb.close()
    return mapping, issues


def load_holidays(path: str | Path) -> tuple[set[datetime.date], list[LoadIssue]]:
    """Load company_holidays.csv into a set of holiday dates."""
    path = Path(path)
    holidays: set[datetime.date] = set()
    issues: list[LoadIssue] = []

    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing = [c for c in HOLIDAY_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"{path.name} is missing expected column(s): {missing}")

        for row_num, row in enumerate(reader, start=2):
            raw_date = row["Date"].strip()
            parsed = _parse_date(raw_date)
            if parsed is None:
                issues.append(LoadIssue("company_holidays.csv", f"row {row_num}",
                                         "Date", f"unparseable date: {raw_date!r}"))
                continue
            holidays.add(parsed)

    return holidays, issues
