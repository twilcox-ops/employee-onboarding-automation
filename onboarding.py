"""Employee Onboarding Automation.

Stage 1: load and validate the three synthetic_corpus/ input files.
Stage 2: business decisions that can be made from that loaded data alone —
scope, scheduling, group mapping, corporate-card validation, and duplicate
handling. Nothing here calls Entra, sends a notification, writes an audit
record, or executes an onboarding step — those are later stages.
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


# ---------------------------------------------------------------------------
# Stage 2: business decisions
#
# Every function below is a pure decision based only on already-loaded data
# (a request, the group mapping, the holiday set, or the batch of requests).
# None of them talk to Entra, send a notification, or write an audit record.
# ---------------------------------------------------------------------------

# REQUIREMENTS.md, Scope: only Regular Full-Time is in scope initially.
IN_SCOPE_EMPLOYMENT_TYPE = "Regular Full-Time"

# REQUIREMENTS.md, Corporate Card: HR provides this as Yes/No.
CARD_SETUP_REQUIRED = "setup_required"
CARD_NO_ACTION = "no_action"
CARD_MANUAL_REVIEW = "manual_review"


def is_in_scope(request: NewHireRequest) -> bool:
    """True if this request's employment type is in scope for automation.

    REQUIREMENTS.md's Scope section names "regular full-time employees" as
    in scope, and separately excludes Contractors, interns, temporary
    workers, and rehires. It does not name every possible Employment Type
    value, so this checks for the one in-scope value rather than trying to
    enumerate every excluded one.
    """
    return request.employment_type == IN_SCOPE_EMPLOYMENT_TYPE


def find_duplicate_request_ids(requests: list[NewHireRequest]) -> set[str]:
    """Return the Request IDs that duplicate an earlier request in this same
    batch — same Employee ID and Start Date as one already seen.

    REQUIREMENTS.md says a second request for the same employee and start
    date must not start a second onboarding, but doesn't say which of two
    such requests should be treated as the one to process. [Technical
    recommendation] The first one encountered (in load order) is kept as
    the primary request; any later one with the same key is flagged as a
    duplicate here.

    A request whose Start Date couldn't be parsed (None) is left out of
    this comparison entirely — there's no reliable value to match on, and
    guessing which request it duplicates isn't something the requirements
    call for.
    """
    seen: set[tuple[str, datetime.date]] = set()
    duplicates: set[str] = set()

    for request in requests:
        if request.start_date is None:
            continue
        key = (request.employee_id, request.start_date)
        if key in seen:
            duplicates.add(request.request_id)
        else:
            seen.add(key)

    return duplicates


def is_business_day(day: datetime.date, holidays: set[datetime.date]) -> bool:
    """REQUIREMENTS.md, Scheduling: weekends and company holidays do not
    count as business days."""
    return day.weekday() < 5 and day not in holidays


def compute_onboarding_date(
    start_date: datetime.date, holidays: set[datetime.date]
) -> datetime.date:
    """One business day before start_date, moved earlier over any run of
    weekend/holiday days, per REQUIREMENTS.md's Scheduling section and its
    two worked examples (Monday start -> previous Friday; Tuesday start
    with Monday as a holiday -> previous Friday).
    """
    day = start_date - datetime.timedelta(days=1)
    while not is_business_day(day, holidays):
        day -= datetime.timedelta(days=1)
    return day


def is_late(hr_ready_date: datetime.date, onboarding_date: datetime.date) -> bool:
    """True if HR marked the employee ready after the normal onboarding day
    had already passed (including after the start date itself), per
    REQUIREMENTS.md's Scheduling section.

    [Technical recommendation] "Already passed" is read as strictly after
    the computed onboarding day — HR marking ready on the onboarding day
    itself is on time, not late. REQUIREMENTS.md does not spell out this
    exact boundary.
    """
    return hr_ready_date > onboarding_date


def lookup_groups(
    department: str, job_role: str, mapping: dict[tuple[str, str], list[str]]
) -> list[str] | None:
    """The required groups for a department/job role, or None if that
    combination isn't in the mapping.

    REQUIREMENTS.md, Groups: unknown/unmapped combinations go to manual
    review — returning None (rather than an empty list or a guess) lets the
    caller apply that rule.
    """
    return mapping.get((department, job_role))


def corporate_card_decision(value: str) -> str:
    """REQUIREMENTS.md, Corporate Card: Yes -> setup required, No -> no
    action, missing/invalid -> manual review.

    [Technical recommendation] "Missing/invalid" is read as anything other
    than an exact "Yes" or "No" — REQUIREMENTS.md doesn't enumerate every
    value that counts as invalid, so this doesn't try to special-case
    values like "Pending" or "Y"; it treats them the same as blank.
    """
    if value == "Yes":
        return CARD_SETUP_REQUIRED
    if value == "No":
        return CARD_NO_ACTION
    return CARD_MANUAL_REVIEW


@dataclass
class RequestDecision:
    """The business facts Stage 2 can determine for one request, before any
    external-system action is attempted.
    """
    request_id: str
    employee_id: str
    in_scope: bool
    is_duplicate: bool
    onboarding_date: datetime.date | None
    is_late: bool
    required_groups: list[str] | None
    card_action: str
    manual_review_reasons: list[str]

    @property
    def needs_manual_review(self) -> bool:
        return bool(self.manual_review_reasons)


def evaluate_request(
    request: NewHireRequest,
    mapping: dict[tuple[str, str], list[str]],
    holidays: set[datetime.date],
    duplicate_request_ids: set[str],
) -> RequestDecision:
    """Combine the Stage 2 decisions for one request into a single result.

    This only computes facts and reasons — it does not decide what to do
    with them (skip, notify, execute a step); that's later-stage
    orchestration.
    """
    reasons: list[str] = []

    in_scope = is_in_scope(request)
    if not in_scope:
        # An out-of-scope employment type is a nonstandard case, which
        # REQUIREMENTS.md's General Rule routes to manual review.
        reasons.append(f"employment type '{request.employment_type}' is not in scope")

    is_duplicate = request.request_id in duplicate_request_ids
    if is_duplicate:
        reasons.append("duplicate request: same employee and start date already submitted")

    onboarding_date: datetime.date | None = None
    late = False
    if request.start_date is None:
        reasons.append("start date could not be determined")
    else:
        onboarding_date = compute_onboarding_date(request.start_date, holidays)
        if request.hr_ready_date is not None:
            late = is_late(request.hr_ready_date, onboarding_date)

    required_groups = lookup_groups(request.department, request.job_role, mapping)
    if required_groups is None:
        reasons.append(
            f"no group mapping for department '{request.department}' "
            f"/ job role '{request.job_role}'"
        )

    card_action = corporate_card_decision(request.corporate_card_required)
    if card_action == CARD_MANUAL_REVIEW:
        reasons.append(
            f"corporate card required value is missing or invalid: "
            f"{request.corporate_card_required!r}"
        )

    return RequestDecision(
        request_id=request.request_id,
        employee_id=request.employee_id,
        in_scope=in_scope,
        is_duplicate=is_duplicate,
        onboarding_date=onboarding_date,
        is_late=late,
        required_groups=required_groups,
        card_action=card_action,
        manual_review_reasons=reasons,
    )


def evaluate_all(
    requests: list[NewHireRequest],
    mapping: dict[tuple[str, str], list[str]],
    holidays: set[datetime.date],
) -> list[RequestDecision]:
    """Evaluate every request in a batch, sharing one duplicate-detection
    pass across all of them (duplicate status can only be known by looking
    at the whole batch together)."""
    duplicate_request_ids = find_duplicate_request_ids(requests)
    return [
        evaluate_request(request, mapping, holidays, duplicate_request_ids)
        for request in requests
    ]
