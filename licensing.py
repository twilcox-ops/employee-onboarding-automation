"""Stage 4: the Microsoft 365 E3 licensing step.

REQUIREMENTS.md, Standard Onboarding, step 2: "Assign Microsoft 365 E3 —
verify the license is actually assigned."

No real M365/Graph integration exists yet, so this mirrors Stage 3's
approach: a small in-memory simulated service (`SimulatedLicensingService`)
gives `evaluate_license_step` somewhere concrete to assign and look up
licenses. It is not a mock of the real Graph API.

Only the E3 licensing step lives here. Group assignment, corporate card,
notifications, audit logging, and orchestration are later work.
"""

from __future__ import annotations

from dataclasses import dataclass

from onboarding import NewHireRequest

E3_SKU = "Microsoft 365 E3"

LICENSE_STATUS_ASSIGNED = "assigned"
LICENSE_STATUS_ALREADY_ASSIGNED = "already_assigned"
LICENSE_STATUS_MANUAL_REVIEW = "manual_review"


class SimulatedLicensingService:
    """In-memory stand-in for the M365 licensing system, keyed by employee ID."""

    def __init__(self) -> None:
        self._licenses: dict[str, set[str]] = {}

    def get_licenses(self, employee_id: str) -> frozenset[str]:
        return frozenset(self._licenses.get(employee_id, set()))

    def assign_license(self, employee_id: str, sku: str) -> None:
        self._licenses.setdefault(employee_id, set()).add(sku)

    def seed_licenses(self, employee_id: str, skus: list[str]) -> None:
        """Directly set an employee's license state, for pre-existing
        conditions in tests rather than for use by the onboarding step."""
        self._licenses[employee_id] = set(skus)


@dataclass
class LicenseResult:
    """The outcome of the E3 licensing step for one request.

    `sku` is always the target license (E3) this step is trying to ensure
    is assigned. `current_licenses` is the employee's actual license state
    at decision time — most useful on a manual_review outcome, where it's
    the structured form of whatever `manual_review_reason` describes in
    prose (e.g. which nonstandard license blocked assignment).
    """
    request_id: str
    employee_id: str
    sku: str
    status: str
    manual_review_reason: str | None
    current_licenses: frozenset[str]

    @property
    def verified(self) -> bool:
        """True if E3 is confirmed assigned — REQUIREMENTS.md requires the
        step be verified, not merely attempted."""
        return self.status in (LICENSE_STATUS_ASSIGNED, LICENSE_STATUS_ALREADY_ASSIGNED)


def evaluate_license_step(
    request: NewHireRequest, service: SimulatedLicensingService
) -> LicenseResult:
    """Assign or verify Microsoft 365 E3 for one request.

    - No licenses on record: assign E3, then re-read the license state to
      confirm it actually took (verified, not merely attempted). If it
      didn't take, that's sent to manual review rather than reported as a
      false success.
    - E3 already on record (alone or alongside anything else): nothing to
      assign — recognized as already done. Verified success.
    - Some other, non-E3 license state already on record: nonstandard.
      REQUIREMENTS.md's General Rule says not to guess, so this goes to
      manual review rather than assuming it's safe to add E3 on top of an
      unrecognized license.
    """
    current = service.get_licenses(request.employee_id)

    if E3_SKU in current:
        return LicenseResult(
            request.request_id, request.employee_id, E3_SKU,
            LICENSE_STATUS_ALREADY_ASSIGNED, None, current,
        )

    if current:
        return LicenseResult(
            request.request_id, request.employee_id, E3_SKU,
            LICENSE_STATUS_MANUAL_REVIEW,
            f"unexpected existing license(s) for employee {request.employee_id!r} "
            f"with no E3 present: {sorted(current)}",
            current,
        )

    service.assign_license(request.employee_id, E3_SKU)
    after = service.get_licenses(request.employee_id)
    if E3_SKU in after:
        return LicenseResult(
            request.request_id, request.employee_id, E3_SKU,
            LICENSE_STATUS_ASSIGNED, None, after,
        )

    return LicenseResult(
        request.request_id, request.employee_id, E3_SKU, LICENSE_STATUS_MANUAL_REVIEW,
        f"E3 assignment for employee {request.employee_id!r} did not verify: "
        f"license not present after assignment",
        after,
    )
