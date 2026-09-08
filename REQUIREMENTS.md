# Employee Onboarding Automation — Requirements

> **Status:** Requirements discovery is in progress. This document is the authoritative source for **confirmed** business requirements only. Do not assume rules that are not defined here.

## Goal

Automate routine employee onboarding, verify required steps completed, and send nonstandard or ambiguous cases to IT for manual review.

## Scope

Initial automation is for **regular full-time employees** in **established departments and roles**.

### Not Included Initially

- Contractors, interns, or temporary workers
- Rehires or department transfers
- Specialized licensing
- Project-specific or exceptional access

## HR / Input

HR decides when a hire is ready for onboarding. At that point, the employee is approved to start and has a confirmed start date.

The HR system is the source of truth for:

- Name and employee ID
- Department and job role
- Manager
- Employment type
- Start date
- Corporate Card Required (Yes/No)

**Not yet decided:** How the automation will receive this data.

## Standard Onboarding

For an in-scope employee, complete and **verify** each required step (verified, not merely attempted):

1. Create the Entra ID account — verify the account exists and corresponds to the intended employee.
   - Standard UPN format: `firstname.lastname@company.example`.
   - If the generated UPN is already in use, do not invent an alternative — send the case to IT for manual review.
2. Assign Microsoft 365 E3 — verify the license is actually assigned.
3. Assign standard groups — verify membership in every required mapped group.
4. Set up a corporate card if required — verify through the card system where possible (exact mechanism unresolved).
5. Email the manager only after all required steps are verified successful.
   - The manager's work email is looked up in Entra using the manager information from the onboarding request.
   - If the manager cannot be uniquely identified, do not guess — send the case to IT for manual review.

If a required step fails verification, continue attempting the remaining independent steps where it is safe to do so. If any required step failed, onboarding is incomplete:

- Steps already completed successfully are **not** rolled back due to another step's failure.
- All failed steps are recorded.
- IT is notified.
- The manager completion email is **not** sent.

## Duplicate Requests & Reruns

- A second onboarding request for the same employee and same start date is a **duplicate** and must not start a second onboarding.
- After a failed onboarding, IT must be able to rerun the case.
- On rerun, the automation checks which required steps were already successfully completed and verified, and leaves those unchanged — it continues only with the remaining incomplete steps.
- If the automation cannot confidently determine whether a step was already completed, it stops that case and sends it to IT for manual review rather than guessing.

## Scheduling

- Routine onboarding runs **one business day before** the employee's start date.
- Weekends and company holidays do not count as business days.
- If the calculated onboarding day falls on a weekend or company holiday, move onboarding to the previous business day.
  - Example: Monday start date → onboard the previous Friday.
  - Example: Tuesday start date with Monday as a company holiday → onboard the previous Friday.
- If HR marks an employee ready after the normal onboarding time has already passed — including after the employee's start date has already passed — process onboarding as soon as possible rather than waiting for the next scheduled date. Record the onboarding as late in the audit evidence.
- The company maintains a holiday calendar; how the automation will access it is unresolved.

## Groups

- Standard groups are determined by **department + job role** using IT's existing mapping spreadsheet.
- Unknown/unmapped combinations → manual review.
- Additional or project-specific access stays manual.

## Corporate Card

HR provides **Corporate Card Required** as Yes/No.

| Value | Action |
|---|---|
| Yes | Card setup required |
| No | No card action |
| Missing/invalid | Manual review |

HR determines eligibility. The automation does not.

## Audit / Evidence

IT needs enough information to determine what the automation attempted, what succeeded or failed, and when — without reconstructing it from separate systems. Each onboarding attempt must retain:

- Employee ID and name
- Start date
- Date/time the attempt ran
- Whether it was an initial attempt or a rerun
- Whether the onboarding was processed late (HR marked ready after the normal onboarding time)
- Result of each required step: Entra account, E3 license, standard groups, and corporate card (when applicable)
- Overall result: completed, incomplete/failed, or sent for manual review
- All failed steps and their error/reason, when something fails
- For manual-review cases, the reason the automation could not continue

A record that a step was verified successfully is sufficient — a dump of API responses is not required.

**Not yet decided:** Storage location and retention period.

## General Rule

Do not guess. Missing, invalid, unknown, or nonstandard cases should go to manual review unless a business rule explicitly says otherwise.

## Still To Decide

- How HR data reaches the automation
- Exact contents/quality of the group-mapping spreadsheet
- Corporate-card system and available integration (including exact verification mechanism)
- How the automation will access the company holiday calendar
- Audit/evidence storage location and retention period
