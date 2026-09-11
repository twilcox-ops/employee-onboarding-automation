"""Stage 12 live proof: process one synthetic employee against a real
developer tenant, through the existing onboarding/process flow, unchanged.

Run it once to prove create/assign/add works end to end against a real
tenant. Run it again (same command) to prove rerun/idempotent behavior:
the audit log this script writes is what makes the second run's rerun
detection fire, exactly the way process.py already works for the
simulated services — nothing rerun-specific lives in this script.

Nothing here duplicates business logic. This only:
  - loads the synthetic corpus and picks one request (Stage 1/2, unchanged)
  - builds the three real Graph-backed services (Stage 12, unchanged)
  - calls process_onboarding once (Stages 9-11, unchanged) — which itself
    runs the rerun-aware workflow, audit logging, and notifications
  - prints what came back

Credentials: GRAPH_TENANT_ID, GRAPH_CLIENT_ID, GRAPH_CLIENT_SECRET must be
set as environment variables (never hardcoded, never read from a
committed file). The app registration needs these least-privilege app-only
Graph permissions, admin-consented: User.Create, User.Read.All,
LicenseAssignment.ReadWrite.All, LicenseAssignment.Read.All, and
GroupMember.ReadWrite.All. The target security groups (matching
group_mapping.xlsx — e.g. "Finance-Users") must already exist in that
tenant, as non-dynamic (assigned-membership) groups.

UPN domain: the standard format (REQUIREMENTS.md, "firstname.lastname@
company.example") defaults to ".example", an IANA-reserved TLD that can
never be a verified domain in any real tenant. Set GRAPH_UPN_DOMAIN to
this tenant's real verified domain before running this against a real
tenant — expected_upn() and evaluate_entra_account_step() both accept it
as an optional parameter (default unchanged), threaded through from here.
The manager-notification path needs no such setting: it now uses the
looked-up manager's actual Entra UPN directly rather than reconstructing
one.

No scheduling, batch execution, retries, or production configuration is
added by this script — it processes exactly one request, once per run.
"""

from __future__ import annotations

import datetime
import os
import sys
from pathlib import Path

from graph_client import GraphError
from graph_services import GraphEntraService, GraphGroupService, GraphLicensingService
from notifications import SimulatedNotifier
from onboarding import (
    evaluate_request,
    find_duplicate_request_ids,
    load_group_mapping,
    load_holidays,
    load_new_hires,
)
from process import ProcessResult, process_onboarding

CORPUS_DIR = Path(__file__).parent / "synthetic_corpus"
AUDIT_LOG_PATH = Path(__file__).parent / "graph_demo_audit_log.jsonl"
DEFAULT_REQUEST_ID = "ONB-1001"
UPN_DOMAIN = os.environ.get("GRAPH_UPN_DOMAIN", "company.example")


def print_result(result: ProcessResult) -> None:
    r = result.onboarding_result

    def line(label: str, status: str, verified: bool, reason: str | None) -> str:
        text = f"{label:<18} {status} (verified={verified})"
        return f"{text} — {reason}" if reason else text

    print(line("Entra account:", r.entra.status, r.entra.verified, r.entra.manual_review_reason))
    print(line("M365 E3 license:", r.license.status, r.license.verified, r.license.manual_review_reason))
    print(
        line("Standard groups:", r.groups.status, r.groups.verified, r.groups.manual_review_reason)
        + f" -> {sorted(r.groups.verified_groups)}"
    )
    print(f"{'Corporate card:':<18} {r.card_status}" + (f" — {r.card_note}" if r.card_note else ""))
    print()
    print(f"{'Overall:':<18} {'COMPLETE' if r.completed else 'INCOMPLETE'} "
          f"(audit overall_result={result.audit_record['overall_result']}, "
          f"attempt_type={result.audit_record['attempt_type']})")
    print(f"{'Notification:':<18} {result.notification_outcome.outcome} "
          f"-> {result.notification_outcome.notification.recipient}")
    print(f"Audit record appended to: {AUDIT_LOG_PATH}")


def main() -> None:
    request_id = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_REQUEST_ID

    hires, _ = load_new_hires(CORPUS_DIR / "new_hires.csv")
    mapping, _ = load_group_mapping(CORPUS_DIR / "group_mapping.xlsx")
    holidays, _ = load_holidays(CORPUS_DIR / "company_holidays.csv")

    try:
        request = next(h for h in hires if h.request_id == request_id)
    except StopIteration:
        raise SystemExit(f"No request {request_id!r} found in synthetic_corpus/new_hires.csv")

    duplicate_ids = find_duplicate_request_ids(hires)
    decision = evaluate_request(request, mapping, holidays, duplicate_ids)

    print(f"--- Live Graph proof: {request.request_id} "
          f"({request.first_name} {request.last_name}, {request.employee_id}) ---")
    print(f"Department/Role:  {request.department} / {request.job_role}")
    print(f"Manager:          {request.manager}")
    print(f"Required groups:  {decision.required_groups}")
    print(f"Corporate card:   {request.corporate_card_required}")
    if decision.manual_review_reasons:
        print("NOTE: Stage 2 already flags this request for manual review "
              "for reasons unrelated to Graph:")
        for reason in decision.manual_review_reasons:
            print(f"  - {reason}")
    upn = f"{request.first_name.lower()}.{request.last_name.lower()}@{UPN_DOMAIN}"
    if UPN_DOMAIN == "company.example":
        print(
            f"NOTE: creating the account at \"{upn}\" — \".example\" cannot be a "
            "verified domain in a real tenant, so account creation will likely "
            "fail here. Set GRAPH_UPN_DOMAIN to this tenant's real verified "
            "domain to fix that."
        )
    else:
        print(f"Account will be created at: {upn} (from GRAPH_UPN_DOMAIN)")
    print()

    try:
        entra_service = GraphEntraService()
        license_service = GraphLicensingService()
        group_service = GraphGroupService()
        notifier = SimulatedNotifier()

        result = process_onboarding(
            request, decision, entra_service, license_service, group_service,
            notifier, mapping, datetime.datetime.now(), audit_log_path=AUDIT_LOG_PATH,
            domain=UPN_DOMAIN,
        )
    except GraphError as exc:
        # The one place this script departs from "let it propagate": a
        # missing-credential or Graph-call failure is the single most
        # common first-run outcome, and a raw traceback isn't the "clear
        # result" this script promises. Nothing about *what happened* is
        # hidden — the same GraphError message is printed either way.
        raise SystemExit(f"Graph call failed: {exc}") from None

    print_result(result)


if __name__ == "__main__":
    main()
