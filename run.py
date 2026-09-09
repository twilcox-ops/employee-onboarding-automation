"""Entry point: run Stage 1 (load/validate) and Stage 2 (business decisions)
against synthetic_corpus/ and print a summary. No external-system actions
(Entra, notifications, audit logging) happen here yet.
"""

from pathlib import Path

from onboarding import evaluate_all, load_group_mapping, load_holidays, load_new_hires

CORPUS_DIR = Path(__file__).parent / "synthetic_corpus"


def main() -> None:
    hires, hire_issues = load_new_hires(CORPUS_DIR / "new_hires.csv")
    mapping, mapping_issues = load_group_mapping(CORPUS_DIR / "group_mapping.xlsx")
    holidays, holiday_issues = load_holidays(CORPUS_DIR / "company_holidays.csv")

    print("--- Stage 1: load ---")
    print(f"new_hires.csv:        {len(hires)} request(s) loaded")
    print(f"group_mapping.xlsx:   {len(mapping)} department/role mapping(s) loaded")
    print(f"company_holidays.csv: {len(holidays)} holiday date(s) loaded")

    all_issues = hire_issues + mapping_issues + holiday_issues
    if all_issues:
        print(f"\n{len(all_issues)} load issue(s) found:")
        for issue in all_issues:
            print(f"  [{issue.source}] {issue.identifier} — {issue.field}: {issue.problem}")
    else:
        print("\nNo load issues found.")

    print("\n--- Stage 2: business decisions ---")
    decisions = evaluate_all(hires, mapping, holidays)

    out_of_scope = [d for d in decisions if not d.in_scope]
    duplicates = [d for d in decisions if d.is_duplicate]
    unmapped = [d for d in decisions if d.required_groups is None]
    invalid_card = [d for d in decisions if d.card_action == "manual_review"]
    late = [d for d in decisions if d.is_late]
    needs_review = [d for d in decisions if d.needs_manual_review]

    print(f"Out-of-scope requests:                 {len(out_of_scope)}")
    print(f"Duplicate requests:                    {len(duplicates)}")
    print(f"Unmapped department/role combinations: {len(unmapped)}")
    print(f"Missing/invalid corporate card values:  {len(invalid_card)}")
    print(f"Late onboarding (HR marked ready late): {len(late)}")
    print(f"Requests needing manual review (any reason): {len(needs_review)}")

    if needs_review:
        print("\nManual review detail:")
        for d in needs_review:
            for reason in d.manual_review_reasons:
                print(f"  {d.request_id} ({d.employee_id}): {reason}")


if __name__ == "__main__":
    main()
