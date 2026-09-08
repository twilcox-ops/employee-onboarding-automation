"""Stage 1 entry point: load and validate the three synthetic_corpus/ files
and print a summary. No onboarding decisions are made yet — later stages
will add scope/duplicate/scheduling/group/card logic and orchestration.
"""

from pathlib import Path

from onboarding import load_group_mapping, load_holidays, load_new_hires

CORPUS_DIR = Path(__file__).parent / "synthetic_corpus"


def main() -> None:
    hires, hire_issues = load_new_hires(CORPUS_DIR / "new_hires.csv")
    mapping, mapping_issues = load_group_mapping(CORPUS_DIR / "group_mapping.xlsx")
    holidays, holiday_issues = load_holidays(CORPUS_DIR / "company_holidays.csv")

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


if __name__ == "__main__":
    main()
