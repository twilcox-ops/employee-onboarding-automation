"""Stage 12: real Microsoft Graph-backed services.

Drop-in replacements for entra.SimulatedEntraService, licensing.
SimulatedLicensingService, and groups.SimulatedGroupService — same method
names and signatures, built on graph_client.GraphClient instead of an
in-memory dict. evaluate_entra_account_step, evaluate_license_step, and
evaluate_group_step (and everything built on them: workflow, rerun,
process, batch, notifications) are untouched and don't know or care which
kind of service they were given.

These are deliberately thin: each method is a small, direct translation
between the existing interface and the Graph calls that satisfy it. No
new business rules live here — an unresolvable identity (no matching
employeeId, no matching group displayName) surfaces as a
graph_client.GraphError and is left to propagate, exactly the way an
unexpected exception during a live attempt already does at the batch
layer (Stage 11) today.
"""

from __future__ import annotations

import secrets
import string
import time

from entra import EntraAccount
from graph_client import GraphClient, GraphError, odata_escape
from licensing import E3_SKU

E3_SKU_PART_NUMBER = "SPE_E3"  # Graph's identifier for the Microsoft 365 E3 SKU

# checkMemberGroups has no published latency guarantee right after a write
# (Microsoft's own docs say so) — a group add can succeed while the very
# next membership check still reads stale. These bound how long
# GraphGroupService.add_member waits for its own write to become visible.
GROUP_ADD_VERIFY_ATTEMPTS = 3
GROUP_ADD_VERIFY_RETRY_DELAY_SECONDS = 1


def _generate_temp_password() -> str:
    """A one-time password meeting typical Entra complexity rules
    (length, and at least one of each character class). The account is
    created with forceChangePasswordNextSignIn, so this value is never
    meant to be used beyond the very first sign-in."""
    alphabet = string.ascii_uppercase, string.ascii_lowercase, string.digits, "!@#$%^&*"
    required = [secrets.choice(chars) for chars in alphabet]
    remaining = [secrets.choice(string.ascii_letters + string.digits) for _ in range(12)]
    password = required + remaining
    secrets.SystemRandom().shuffle(password)
    return "".join(password)


class GraphEntraService:
    """Satisfies the same interface as entra.SimulatedEntraService."""

    def __init__(self, client: GraphClient | None = None) -> None:
        self._client = client or GraphClient()

    def find_by_upn(self, upn: str) -> EntraAccount | None:
        try:
            data = self._client.get(
                f"/users/{upn}",
                params={"$select": "userPrincipalName,employeeId,givenName,surname"},
            )
        except GraphError as exc:
            if exc.status_code == 404:
                return None
            raise
        return EntraAccount(
            upn=data["userPrincipalName"],
            employee_id=data.get("employeeId"),
            first_name=data.get("givenName") or "",
            last_name=data.get("surname") or "",
        )

    def find_by_display_name(self, display_name: str) -> list[EntraAccount]:
        # Mirrors SimulatedEntraService.find_by_display_name exactly:
        # a "first last" match, one space, nothing fuzzier.
        parts = display_name.split(" ", 1)
        if len(parts) != 2 or not all(parts):
            return []
        given, surname = parts

        data = self._client.get(
            "/users",
            params={
                "$filter": (
                    f"givenName eq '{odata_escape(given)}' and "
                    f"surname eq '{odata_escape(surname)}'"
                ),
                "$select": "userPrincipalName,employeeId,givenName,surname",
            },
        )
        return [
            EntraAccount(
                upn=u["userPrincipalName"],
                employee_id=u.get("employeeId"),
                first_name=u.get("givenName") or "",
                last_name=u.get("surname") or "",
            )
            for u in data.get("value", [])
        ]

    def create_account(
        self, upn: str, employee_id: str, first_name: str, last_name: str
    ) -> EntraAccount:
        mail_nickname = upn.split("@", 1)[0].replace(".", "")
        body = {
            "accountEnabled": True,
            "displayName": f"{first_name} {last_name}",
            "givenName": first_name,
            "surname": last_name,
            "mailNickname": mail_nickname,
            "userPrincipalName": upn,
            "employeeId": employee_id,
            "passwordProfile": {
                "forceChangePasswordNextSignIn": True,
                "password": _generate_temp_password(),
            },
        }
        data = self._client.post("/users", body)
        return EntraAccount(
            upn=data["userPrincipalName"],
            employee_id=data.get("employeeId"),
            first_name=data.get("givenName") or first_name,
            last_name=data.get("surname") or last_name,
        )


class GraphLicensingService:
    """Satisfies the same interface as licensing.SimulatedLicensingService."""

    def __init__(self, client: GraphClient | None = None) -> None:
        self._client = client or GraphClient()
        self._sku_id_by_part_number: dict[str, str] | None = None  # cached on first use

    def _sku_catalog(self) -> dict[str, str]:
        """{skuPartNumber: skuId} for this tenant's subscribed SKUs,
        fetched once and cached — the catalog doesn't change within a run."""
        if self._sku_id_by_part_number is None:
            data = self._client.get("/subscribedSkus", params={"$select": "skuId,skuPartNumber"})
            self._sku_id_by_part_number = {
                sku["skuPartNumber"]: sku["skuId"] for sku in data.get("value", [])
            }
        return self._sku_id_by_part_number

    def get_licenses(self, employee_id: str) -> frozenset[str]:
        """Translates Graph's tenant-specific skuIds back into the same
        vocabulary the simulated service and evaluate_license_step already
        use — E3_SKU for the E3 SKU, the raw skuPartNumber for anything
        else — so the business logic's `E3_SKU in current` check needs no
        changes to work against a real tenant."""
        user_id = self._client.resolve_user_id_by_employee_id(employee_id)
        data = self._client.get(f"/users/{user_id}", params={"$select": "assignedLicenses"})
        part_number_by_sku_id = {v: k for k, v in self._sku_catalog().items()}

        licenses = set()
        for assigned in data.get("assignedLicenses", []):
            part_number = part_number_by_sku_id.get(assigned["skuId"], assigned["skuId"])
            licenses.add(E3_SKU if part_number == E3_SKU_PART_NUMBER else part_number)
        return frozenset(licenses)

    def assign_license(self, employee_id: str, sku: str) -> None:
        if sku != E3_SKU:
            raise ValueError(f"GraphLicensingService only knows how to assign {E3_SKU!r}, got {sku!r}")

        user_id = self._client.resolve_user_id_by_employee_id(employee_id)
        sku_id = self._sku_catalog().get(E3_SKU_PART_NUMBER)
        if sku_id is None:
            raise GraphError(
                f"no subscribed SKU found in this tenant with part number "
                f"{E3_SKU_PART_NUMBER!r} ({E3_SKU})"
            )
        self._client.post(
            f"/users/{user_id}/assignLicense",
            {"addLicenses": [{"skuId": sku_id}], "removeLicenses": []},
        )


class GraphGroupService:
    """Satisfies the same interface as groups.SimulatedGroupService."""

    def __init__(self, client: GraphClient | None = None) -> None:
        self._client = client or GraphClient()
        self._group_id_by_name: dict[str, str] = {}  # cached on first use, per group name

    def _group_id(self, group: str) -> str:
        if group not in self._group_id_by_name:
            self._group_id_by_name[group] = self._client.resolve_group_id_by_display_name(group)
        return self._group_id_by_name[group]

    def is_member(self, employee_id: str, group: str) -> bool:
        user_id = self._client.resolve_user_id_by_employee_id(employee_id)
        group_id = self._group_id(group)
        result = self._client.post(f"/users/{user_id}/checkMemberGroups", {"groupIds": [group_id]})
        return group_id in result.get("value", [])

    def add_member(self, employee_id: str, group: str) -> None:
        user_id = self._client.resolve_user_id_by_employee_id(employee_id)
        group_id = self._group_id(group)
        self._client.post(
            f"/groups/{group_id}/members/$ref",
            {"@odata.id": f"https://graph.microsoft.com/v1.0/directoryObjects/{user_id}"},
        )
        for attempt in range(GROUP_ADD_VERIFY_ATTEMPTS):
            if self.is_member(employee_id, group):
                return
            if attempt < GROUP_ADD_VERIFY_ATTEMPTS - 1:
                time.sleep(GROUP_ADD_VERIFY_RETRY_DELAY_SECONDS)
