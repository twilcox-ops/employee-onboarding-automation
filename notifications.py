"""Stage 8: notifications — IT manual-review alerts and the manager
completion email.

REQUIREMENTS.md, Standard Onboarding, step 5: "Email the manager only
after all required steps are verified successful. The manager's work
email is looked up in Entra using the manager information from the
onboarding request. If the manager cannot be uniquely identified, do not
guess — send the case to IT for manual review." And, failure handling:
whenever a required step failed, "IT is notified" and "the manager
completion email is not sent."

The email itself is always simulated/local, never a real email service:
`SimulatedNotifier` just collects `Notification` objects instead of
delivering anything. Manager lookup is different — it goes through
whatever Entra directory `notify_for_attempt` is given (by display name),
the same Stage 3 interface the Entra account step uses, so it's the real
Microsoft Graph directory (`graph_services.GraphEntraService`) whenever
that's what's passed in, not just the simulated one. `notify_for_attempt`
also reuses Stage 6's `OnboardingResult` rather than recomputing any step
outcome.
"""

from __future__ import annotations

from dataclasses import dataclass

from entra import EntraAccount, SimulatedEntraService
from onboarding import NewHireRequest
from workflow import OnboardingResult

NOTIFICATION_IT_REVIEW = "it_manual_review"
NOTIFICATION_MANAGER_COMPLETE = "manager_completion"

MANAGER_LOOKUP_FOUND = "found"
MANAGER_LOOKUP_NOT_FOUND = "not_found"
MANAGER_LOOKUP_AMBIGUOUS = "ambiguous"

OUTCOME_MANAGER_NOTIFIED = "manager_notified"
OUTCOME_IT_NOTIFIED_INCOMPLETE = "it_notified_incomplete"
OUTCOME_IT_NOTIFIED_MANAGER_UNRESOLVED = "it_notified_manager_unresolved"


@dataclass
class Notification:
    """What would have been sent, had this been a real notification."""
    kind: str
    request_id: str
    employee_id: str
    recipient: str
    subject: str
    summary: str


class SimulatedNotifier:
    """In-memory stand-in for a real notification/email system — collects
    what would have been sent instead of delivering anything."""

    def __init__(self) -> None:
        self.sent: list[Notification] = []

    def send(self, notification: Notification) -> None:
        self.sent.append(notification)


@dataclass
class ManagerLookupResult:
    status: str
    account: EntraAccount | None
    reason: str | None


@dataclass
class NotificationOutcome:
    """What happened for one attempt: the notification sent, plus the two
    facts that matter beyond it — whether onboarding itself completed
    (kept distinct so a manager-lookup failure never reads as an
    onboarding failure) and the manager lookup that was or wasn't done."""
    notification: Notification
    onboarding_completed: bool
    outcome: str
    manager_lookup: ManagerLookupResult | None

    @property
    def needs_manual_review(self) -> bool:
        """True when onboarding completed but the manager couldn't be
        uniquely identified — completed and still needing manual review
        aren't mutually exclusive, so callers shouldn't have to infer this
        from `outcome`."""
        return self.outcome == OUTCOME_IT_NOTIFIED_MANAGER_UNRESOLVED


def lookup_manager(manager_name: str, entra_service: SimulatedEntraService) -> ManagerLookupResult:
    """Look up the manager's Entra account by the plain-text display name
    from the onboarding request. Exactly one match is usable; zero or more
    than one means it can't be uniquely identified."""
    matches = entra_service.find_by_display_name(manager_name)
    if len(matches) == 1:
        return ManagerLookupResult(MANAGER_LOOKUP_FOUND, matches[0], None)
    if not matches:
        return ManagerLookupResult(
            MANAGER_LOOKUP_NOT_FOUND, None,
            f"no Entra account found for manager {manager_name!r}",
        )
    return ManagerLookupResult(
        MANAGER_LOOKUP_AMBIGUOUS, None,
        f"{len(matches)} Entra accounts found for manager {manager_name!r}; "
        f"cannot uniquely identify",
    )


def _summarize_steps(result: OnboardingResult) -> str:
    """Brief text summarizing what succeeded and what didn't — enough for
    IT to see the shape of the attempt without an API-response dump."""
    named_steps = {
        "entra_account": result.entra,
        "license": result.license,
        "groups": result.groups,
    }

    verified = [name for name, step in named_steps.items() if step.verified]
    if result.card_status == "not_applicable":
        verified.append("corporate_card")

    failures = [
        f"{name} ({step.status}): {step.manual_review_reason}"
        for name, step in named_steps.items() if not step.verified
    ]
    if "corporate_card" in result.failed_steps:
        failures.append(f"corporate_card ({result.card_status}): {result.card_note}")

    parts = []
    if verified:
        parts.append("Verified: " + ", ".join(verified))
    if failures:
        parts.append("Needs attention: " + "; ".join(failures))
    return ". ".join(parts)


def notify_for_attempt(
    request: NewHireRequest,
    workflow_result: OnboardingResult,
    entra_service: SimulatedEntraService,
    notifier: SimulatedNotifier,
) -> NotificationOutcome:
    """Send the one notification this attempt calls for, and return the
    outcome.

    Applies to every attempt, initial or rerun alike — nothing here
    dedupes or suppresses across reruns, so a rerun that's still
    incomplete gets its own IT notification just like the first attempt.

    - Not every required step verified: one IT notification, summarizing
      what succeeded and what needs attention. No manager lookup is even
      attempted — REQUIREMENTS.md only emails the manager once every
      required step has verified.
    - Every step verified and the manager is uniquely identified: one
      manager completion email.
    - Every step verified but the manager can't be uniquely identified
      (zero or multiple Entra matches): one IT notification. Onboarding
      itself is still complete — `onboarding_completed` stays True — but
      the case as a whole needs manual review because no completion email
      could be sent.
    """
    if not workflow_result.completed:
        notification = Notification(
            NOTIFICATION_IT_REVIEW, request.request_id, request.employee_id, "IT",
            f"Onboarding incomplete: {request.employee_id} "
            f"({request.first_name} {request.last_name})",
            _summarize_steps(workflow_result),
        )
        notifier.send(notification)
        return NotificationOutcome(
            notification, onboarding_completed=False,
            outcome=OUTCOME_IT_NOTIFIED_INCOMPLETE, manager_lookup=None,
        )

    manager_lookup = lookup_manager(request.manager, entra_service)

    if manager_lookup.status == MANAGER_LOOKUP_FOUND:
        notification = Notification(
            NOTIFICATION_MANAGER_COMPLETE, request.request_id, request.employee_id,
            manager_lookup.account.upn,
            f"Onboarding complete: {request.first_name} {request.last_name}",
            _summarize_steps(workflow_result),
        )
        notifier.send(notification)
        return NotificationOutcome(
            notification, onboarding_completed=True,
            outcome=OUTCOME_MANAGER_NOTIFIED, manager_lookup=manager_lookup,
        )

    notification = Notification(
        NOTIFICATION_IT_REVIEW, request.request_id, request.employee_id, "IT",
        f"Onboarding complete but manager unresolved: {request.employee_id}",
        f"All onboarding steps completed and verified for "
        f"{request.first_name} {request.last_name}. Manager completion "
        f"email could not be sent: {manager_lookup.reason}.",
    )
    notifier.send(notification)
    return NotificationOutcome(
        notification, onboarding_completed=True,
        outcome=OUTCOME_IT_NOTIFIED_MANAGER_UNRESOLVED, manager_lookup=manager_lookup,
    )
