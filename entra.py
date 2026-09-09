"""Stage 3: the Entra ID account step.

REQUIREMENTS.md, Standard Onboarding, step 1: "Create the Entra ID account —
verify the account exists and corresponds to the intended employee," using
the standard UPN format `firstname.lastname@company.example`, and "If the
generated UPN is already in use, do not invent an alternative — send the
case to IT for manual review."

There is no real Entra integration yet, so this module includes a small
in-memory simulated directory (`SimulatedEntraService`) to develop and test
the decision logic against. It is not a mock of the real Microsoft Graph
API — it exists only so `evaluate_entra_account_step` has something
concrete to create accounts in and look them up from, until the real
integration is built.

Only the Entra account step lives here. Licensing, groups, corporate card,
notifications, and the orchestration that ties every step together for a
request (scope/duplicate gating, retries, audit logging) are later work.
"""

from __future__ import annotations

from dataclasses import dataclass

from onboarding import NewHireRequest

# Result statuses for evaluate_entra_account_step.
ENTRA_STATUS_CREATED = "created"
ENTRA_STATUS_ALREADY_EXISTS = "already_exists"
ENTRA_STATUS_MANUAL_REVIEW = "manual_review"


@dataclass
class EntraAccount:
    """One account in the simulated directory."""
    upn: str
    employee_id: str | None
    first_name: str
    last_name: str


class SimulatedEntraService:
    """In-memory stand-in for the Entra directory, keyed by UPN.

    Real Entra would be queried by UPN and would return whatever account
    (if any) is registered there; this simulation does the same, just
    against a dict instead of a live directory.
    """

    def __init__(self) -> None:
        self._accounts: dict[str, EntraAccount] = {}

    def find_by_upn(self, upn: str) -> EntraAccount | None:
        return self._accounts.get(upn)

    def create_account(
        self, upn: str, employee_id: str, first_name: str, last_name: str
    ) -> EntraAccount:
        """Create a new account at this UPN.

        Mirrors what a real directory would refuse: creating over a UPN
        that's already taken. Callers are expected to check
        `find_by_upn` first (as `evaluate_entra_account_step` does) rather
        than relying on this to detect the collision.
        """
        if upn in self._accounts:
            raise ValueError(f"an account already exists for UPN {upn!r}")
        account = EntraAccount(
            upn=upn, employee_id=employee_id, first_name=first_name, last_name=last_name
        )
        self._accounts[upn] = account
        return account

    def seed_account(
        self, upn: str, employee_id: str | None, first_name: str = "", last_name: str = ""
    ) -> EntraAccount:
        """Directly register an account, bypassing create_account's
        already-exists check. For setting up pre-existing directory state
        in tests (e.g. simulating a prior onboarding or an unrelated
        legacy account) rather than for use by the onboarding step itself.
        """
        account = EntraAccount(
            upn=upn, employee_id=employee_id, first_name=first_name, last_name=last_name
        )
        self._accounts[upn] = account
        return account


@dataclass
class EntraAccountResult:
    """The outcome of the Entra account step for one request."""
    request_id: str
    employee_id: str
    upn: str
    status: str
    manual_review_reason: str | None

    @property
    def verified(self) -> bool:
        """True if the account is confirmed to exist and correspond to the
        intended employee — REQUIREMENTS.md requires the step be verified,
        not merely attempted."""
        return self.status in (ENTRA_STATUS_CREATED, ENTRA_STATUS_ALREADY_EXISTS)


def expected_upn(first_name: str, last_name: str) -> str:
    """REQUIREMENTS.md's standard UPN format: firstname.lastname@company.example."""
    return f"{first_name.strip().lower()}.{last_name.strip().lower()}@company.example"


def evaluate_entra_account_step(
    request: NewHireRequest, service: SimulatedEntraService
) -> EntraAccountResult:
    """Create or verify the Entra ID account for one request.

    Four possible outcomes:

    - No account exists yet at the expected UPN: create one, tagged with
      this request's employee ID. Verified success.
    - An account already exists at the expected UPN and its employee ID
      matches this request: the step was already completed — e.g. this is
      a rerun after an earlier partial failure. Nothing is created; this is
      verified success, per REQUIREMENTS.md's rerun rule that already-
      completed steps are left unchanged.
    - An account already exists at the expected UPN for a *different*
      employee ID: a UPN collision. REQUIREMENTS.md says not to invent an
      alternative UPN — this is sent to manual review.
    - An account already exists at the expected UPN with no employee ID on
      record, so it can't be confirmed whose account it is: ambiguous.
      REQUIREMENTS.md's General Rule says not to guess, so this is also
      sent to manual review rather than assuming it's a match or a
      collision.
    """
    upn = expected_upn(request.first_name, request.last_name)
    existing = service.find_by_upn(upn)

    if existing is None:
        service.create_account(
            upn=upn,
            employee_id=request.employee_id,
            first_name=request.first_name,
            last_name=request.last_name,
        )
        return EntraAccountResult(
            request.request_id, request.employee_id, upn, ENTRA_STATUS_CREATED, None
        )

    if not existing.employee_id:
        return EntraAccountResult(
            request.request_id, request.employee_id, upn, ENTRA_STATUS_MANUAL_REVIEW,
            f"an account already exists for UPN {upn!r} but it has no employee ID "
            f"on record, so it cannot be confirmed to correspond to employee "
            f"{request.employee_id!r}",
        )

    if existing.employee_id == request.employee_id:
        return EntraAccountResult(
            request.request_id, request.employee_id, upn, ENTRA_STATUS_ALREADY_EXISTS, None
        )

    return EntraAccountResult(
        request.request_id, request.employee_id, upn, ENTRA_STATUS_MANUAL_REVIEW,
        f"UPN {upn!r} is already in use by a different employee "
        f"({existing.employee_id!r})",
    )
