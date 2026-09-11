"""Stage 5: the standard group assignment step.

REQUIREMENTS.md, Standard Onboarding, step 3: "Assign standard groups —
verify membership in every required mapped group." And, Groups: "Unknown/
unmapped combinations → manual review."

This mirrors Stages 3/4: a small in-memory simulated service
(`SimulatedGroupService`) gives `evaluate_group_step` somewhere concrete to
add and check memberships. A real Microsoft Graph-backed equivalent
(`graph_services.GraphGroupService`) satisfies the same interface and is a
drop-in replacement; `evaluate_group_step` itself needed no changes to
work against it.

Required groups come from the existing Stage 2 mapping lookup
(`onboarding.lookup_groups`) rather than reloading or re-deriving them here.

Only the group assignment step lives here. Corporate card, notifications,
audit logging, and orchestration live in their own modules.
"""

from __future__ import annotations

from dataclasses import dataclass

from onboarding import NewHireRequest, lookup_groups

GROUP_STATUS_VERIFIED = "verified"
GROUP_STATUS_FAILED = "failed"
GROUP_STATUS_MANUAL_REVIEW = "manual_review"


class SimulatedGroupService:
    """In-memory stand-in for the group directory, keyed by employee ID."""

    def __init__(self) -> None:
        self._memberships: dict[str, set[str]] = {}

    def is_member(self, employee_id: str, group: str) -> bool:
        return group in self._memberships.get(employee_id, set())

    def add_member(self, employee_id: str, group: str) -> None:
        self._memberships.setdefault(employee_id, set()).add(group)

    def seed_membership(self, employee_id: str, groups: list[str]) -> None:
        """Directly set an employee's group memberships, for pre-existing
        conditions in tests rather than for use by the onboarding step."""
        self._memberships[employee_id] = set(groups)


@dataclass
class GroupResult:
    """The outcome of the group assignment step for one request."""
    request_id: str
    employee_id: str
    required_groups: list[str] | None
    verified_groups: frozenset[str]
    failed_groups: frozenset[str]
    status: str
    manual_review_reason: str | None

    @property
    def verified(self) -> bool:
        return self.status == GROUP_STATUS_VERIFIED


def evaluate_group_step(
    request: NewHireRequest,
    mapping: dict[tuple[str, str], list[str]],
    service: SimulatedGroupService,
) -> GroupResult:
    """Assign and verify standard group membership for one request.

    - Department/job role isn't in the mapping: required groups are
      unknown. REQUIREMENTS.md says not to guess — manual review.
    - Already a member of a required group: left alone, counted verified.
    - Not yet a member: added, then re-checked to confirm membership
      actually took (verified, not merely attempted).
    - Every required group ends up verified: step succeeds.
    - One or more required groups can't be verified after an add attempt:
      the step fails, but groups that *did* verify are not rolled back —
      each group is independent, same as REQUIREMENTS.md's rule for
      required steps generally.
    - A real integration's service call can raise for one group (e.g. a
      transient directory-consistency issue) where the simulated service
      never does. That's isolated per group here, in this same loop, so
      a group already confirmed earlier in the loop stays confirmed —
      catching this one level up (in workflow.py) couldn't tell which
      groups had already succeeded before the failure.
    """
    required_groups = lookup_groups(request.department, request.job_role, mapping)
    if required_groups is None:
        return GroupResult(
            request.request_id, request.employee_id, None, frozenset(), frozenset(),
            GROUP_STATUS_MANUAL_REVIEW,
            f"no group mapping for department {request.department!r} "
            f"/ job role {request.job_role!r}",
        )

    verified: set[str] = set()
    failed: set[str] = set()
    exception_reasons: dict[str, str] = {}
    for group in required_groups:
        try:
            if not service.is_member(request.employee_id, group):
                service.add_member(request.employee_id, group)
            if service.is_member(request.employee_id, group):
                verified.add(group)
            else:
                failed.add(group)
        except Exception as exc:  # noqa: BLE001 - deliberately broad: isolate this one group's failure
            failed.add(group)
            exception_reasons[group] = str(exc)

    status = GROUP_STATUS_FAILED if failed else GROUP_STATUS_VERIFIED
    reason = None
    if failed:
        parts = [f"membership could not be verified for group(s): {sorted(failed)}"]
        parts += [f"{group}: {why}" for group, why in sorted(exception_reasons.items())]
        reason = "; ".join(parts)

    return GroupResult(
        request.request_id, request.employee_id, required_groups,
        frozenset(verified), frozenset(failed), status, reason,
    )
