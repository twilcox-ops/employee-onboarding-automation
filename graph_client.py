"""Shared Microsoft Graph client: app-only auth, thin REST helpers, and
the identity resolution every Graph-backed service needs (Stage 12's
graph_services.py is built on top of this, unchanged from the simulated
services' business logic).

Nothing here talks about onboarding, Entra accounts, licenses, or groups
as domain concepts — it only knows how to authenticate and make Graph API
calls. That keeps it reusable by all three Graph-backed services and
importable without needing credentials present (they're only required
once a request is actually made).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

import msal

GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = ["https://graph.microsoft.com/.default"]  # app-only: the app's own granted permissions


class GraphError(RuntimeError):
    """A Graph API call failed (auth, HTTP, or a malformed response).

    `status_code` is set when this came from an HTTP error response, so a
    caller can distinguish e.g. 404 (nothing at that address — not
    inherently an error to a caller like find_by_upn) from a genuine
    failure, without parsing the message text.
    """

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def odata_escape(value: str) -> str:
    """Escape a value for use inside an OData $filter string literal.
    Single quotes double up; that's the entire OData v4 escaping rule for
    string literals — no other characters need it."""
    return value.replace("'", "''")


class GraphClient:
    """App-only Microsoft Graph client using client-credentials auth.

    Credentials come from the environment (GRAPH_TENANT_ID, GRAPH_CLIENT_ID,
    GRAPH_CLIENT_SECRET) — never hardcoded, never read from a committed
    file. Reading them is deferred to first use, so importing this module
    or constructing a client doesn't require them to already be set.
    """

    def __init__(
        self,
        tenant_id: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> None:
        self._tenant_id = tenant_id or os.environ.get("GRAPH_TENANT_ID")
        self._client_id = client_id or os.environ.get("GRAPH_CLIENT_ID")
        self._client_secret = client_secret or os.environ.get("GRAPH_CLIENT_SECRET")
        missing = [
            name for name, value in (
                ("GRAPH_TENANT_ID", self._tenant_id),
                ("GRAPH_CLIENT_ID", self._client_id),
                ("GRAPH_CLIENT_SECRET", self._client_secret),
            )
            if not value
        ]
        if missing:
            raise GraphError(
                f"missing Graph credentials: {', '.join(missing)} — set them as "
                f"environment variables (or pass them explicitly) before making "
                f"any Graph API call"
            )

        self._app: msal.ConfidentialClientApplication | None = None  # built lazily; see _get_app

    # --- auth -----------------------------------------------------------

    def _get_app(self) -> msal.ConfidentialClientApplication:
        """The MSAL app, built on first use rather than in __init__.

        MSAL's constructor makes its own network call (OIDC discovery
        against the tenant's authority) — building it eagerly would mean
        constructing a GraphClient always hit the network, even before any
        Graph call was made. Deferring it here keeps construction free of
        network/credential-validity concerns; only an actual request pays
        that cost, and only once.
        """
        if self._app is None:
            try:
                self._app = msal.ConfidentialClientApplication(
                    client_id=self._client_id,
                    client_credential=self._client_secret,
                    authority=f"https://login.microsoftonline.com/{self._tenant_id}",
                )
            except ValueError as exc:
                raise GraphError(f"could not initialize the Graph auth client: {exc}") from exc
        return self._app

    def _get_token(self) -> str:
        """The current access token, acquiring (or reusing MSAL's cached)
        one as needed. MSAL's own in-memory cache means this only
        actually calls out to Azure AD when there's no valid cached token."""
        result = self._get_app().acquire_token_for_client(scopes=GRAPH_SCOPE)
        if "access_token" not in result:
            description = result.get("error_description", result.get("error", "unknown error"))
            raise GraphError(f"failed to acquire a Graph access token: {description}")
        return result["access_token"]

    # --- thin REST helpers ------------------------------------------------

    def _request(self, method: str, path: str, body: dict | None = None) -> dict | None:
        """One authenticated Graph API call. `path` is either an absolute
        URL (as returned by Graph in @odata.nextLink, etc.) or a path
        relative to GRAPH_API_BASE, e.g. "/users/{id}". Returns the parsed
        JSON body, or None for a 204 No Content response."""
        url = path if path.startswith("http") else GRAPH_API_BASE + path
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self._get_token()}")
        if data is not None:
            request.add_header("Content-Type", "application/json")

        try:
            with urllib.request.urlopen(request) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            message = raw.decode("utf-8", errors="replace")
            try:
                message = json.loads(message)["error"]["message"]
            except (json.JSONDecodeError, KeyError, TypeError):
                pass  # fall back to the raw body if it isn't the usual Graph error shape
            raise GraphError(
                f"Graph {method} {path} failed ({exc.code}): {message}", status_code=exc.code
            ) from exc

    def get(self, path: str, params: dict[str, str] | None = None) -> dict | None:
        if params:
            path = f"{path}?{urllib.parse.urlencode(params)}"
        return self._request("GET", path)

    def post(self, path: str, body: dict | None = None) -> dict | None:
        return self._request("POST", path, body)

    # --- identity resolution ---------------------------------------------

    def _resolve_one(
        self, collection: str, property_name: str, value: str, select: str, noun: str
    ) -> str:
        """The Graph object ID of the single `collection` item whose
        `property_name` exactly matches `value` — the shared fetch ->
        0/1/many-match -> raise shape behind both resolve_* methods below.
        Exact match only — no fuzzy/contains/startswith matching — and
        fails clearly rather than guessing on zero or multiple matches.
        `noun` (e.g. "user", "group") only affects the error message.
        """
        result = self.get(
            collection,
            params={"$filter": f"{property_name} eq '{odata_escape(value)}'", "$select": select},
        )
        matches = result.get("value", [])
        if not matches:
            raise GraphError(f"no Graph {noun} found with {property_name} {value!r}")
        if len(matches) > 1:
            raise GraphError(
                f"{len(matches)} Graph {noun}s found with {property_name} {value!r}; "
                f"cannot uniquely resolve"
            )
        return matches[0]["id"]

    def resolve_user_id_by_employee_id(self, employee_id: str) -> str:
        """The Graph object ID of the user whose employeeId property
        exactly matches."""
        return self._resolve_one("/users", "employeeId", employee_id, "id,employeeId", "user")

    def resolve_group_id_by_display_name(self, display_name: str) -> str:
        """The Graph object ID of the group whose displayName property
        exactly matches."""
        return self._resolve_one("/groups", "displayName", display_name, "id,displayName", "group")
