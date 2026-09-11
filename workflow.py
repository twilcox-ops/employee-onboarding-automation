"""Stage 6: tie the existing per-step logic together into one workflow.

REQUIREMENTS.md, Standard Onboarding: run the required steps for an
in-scope request, continuing independent steps even if one fails; overall
onboarding is complete only if every required step is verified. Steps
already completed successfully are not rolled back by another step's
failure.

This reuses each stage's existing step function rather than rewriting any
business logic: Stage 3's `evaluate_entra_account_step`, Stage 4's
`evaluate_license_step`, Stage 5's `evaluate_group_step`, and Stage 2's
`corporate_card_decision`. Callers are expected to only run this for
requests already known to be in scope (`onboarding.is_in_scope`) — this
module doesn't re-decide scope.

There is no real corporate-card integration yet (REQUIREMENTS.md notes the
mechanism is unresolved), so a required card is recorded as not yet
completable rather than attempted. The manager completion email, IT
notification, and audit storage are also not here yet.

A real integration (e.g. a Graph-backed license service) can raise for a
condition the simulated services never do — the target SKU not existing
in this tenant at all, say — rather than returning a graceful failure.
REQUIREMENTS.md's "one required step's failure doesn't stop the others"
applies just as much to that as to an ordinary manual_review return, so
the license step is caught here and turned into one, with the real error
preserved as the reason, instead of aborting the rest of the attempt.
"""

from __future__ import annotations

from dataclasses import dataclass

from entra import EntraAccountResult, SimulatedEntraService, evaluate_entra_account_step
from groups import GroupResult, SimulatedGroupService, evaluate_group_step
from licensing import (
    E3_SKU,
    LICENSE_STATUS_MANUAL_REVIEW,
    LicenseResult,
    SimulatedLicensingService,
    evaluate_license_step,
)
from onboarding import (
    CARD_NO_ACTION,
    CARD_SETUP_REQUIRED,
    NewHireRequest,
    corporate_card_decision,
)

CARD_STATUS_NOT_APPLICABLE = "not_applicable"
CARD_STATUS_PENDING_INTEGRATION = "pending_integration"
CARD_STATUS_MANUAL_REVIEW = "manual_review"


@dataclass
class OnboardingResult:
    """The combined outcome of running every existing step for one
    in-scope request."""
    request_id: str
    employee_id: str
    entra: EntraAccountResult
    license: LicenseResult
    groups: GroupResult
    card_status: str
    card_note: str | None
    completed: bool
    failed_steps: list[str]


def run_onboarding(
    request: NewHireRequest,
    entra_service: SimulatedEntraService,
    license_service: SimulatedLicensingService,
    group_service: SimulatedGroupService,
    mapping: dict[tuple[str, str], list[str]],
    domain: str = "company.example",
) -> OnboardingResult:
    """Run every existing onboarding step for one in-scope request.

    `domain` is only used for the Entra account's UPN and defaults to the
    synthetic "company.example" REQUIREMENTS.md and the simulated tests
    use — a live caller passes its tenant's real verified domain instead.

    Each step is independent — one failing doesn't stop or roll back the
    others. Corporate card has no real integration yet:

    - not required: not applicable, counts as satisfied.
    - required: recorded as pending — the integration doesn't exist yet,
      so this can never verify.
    - missing/invalid value: manual review, same as Stage 2's decision —
      REQUIREMENTS.md says not to guess.

    Overall onboarding is complete only if every step above verified.
    """
    entra_result = evaluate_entra_account_step(request, entra_service, domain)
    try:
        license_result = evaluate_license_step(request, license_service)
    except Exception as exc:  # noqa: BLE001 - deliberately broad: isolate this step's failure
        license_result = LicenseResult(
            request.request_id, request.employee_id, E3_SKU,
            LICENSE_STATUS_MANUAL_REVIEW, str(exc), frozenset(),
        )
    group_result = evaluate_group_step(request, mapping, group_service)

    card_action = corporate_card_decision(request.corporate_card_required)
    if card_action == CARD_NO_ACTION:
        card_status = CARD_STATUS_NOT_APPLICABLE
        card_note = None
    elif card_action == CARD_SETUP_REQUIRED:
        card_status = CARD_STATUS_PENDING_INTEGRATION
        card_note = (
            "corporate card setup is required, but the card system "
            "integration is not implemented yet"
        )
    else:  # CARD_MANUAL_REVIEW
        card_status = CARD_STATUS_MANUAL_REVIEW
        card_note = (
            f"corporate card required value is missing or invalid: "
            f"{request.corporate_card_required!r}"
        )
    card_verified = card_status == CARD_STATUS_NOT_APPLICABLE

    failed_steps = [
        name for name, verified in (
            ("entra_account", entra_result.verified),
            ("license", license_result.verified),
            ("groups", group_result.verified),
            ("corporate_card", card_verified),
        )
        if not verified
    ]

    return OnboardingResult(
        request_id=request.request_id,
        employee_id=request.employee_id,
        entra=entra_result,
        license=license_result,
        groups=group_result,
        card_status=card_status,
        card_note=card_note,
        completed=not failed_steps,
        failed_steps=failed_steps,
    )
