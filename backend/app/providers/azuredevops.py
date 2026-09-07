"""Async read-only client for the Azure DevOps REST API.

Not a `Provider` (see app/providers/base.py) — that Protocol is for locally
installed asset sources scanned off disk, and it is registered explicitly in
`providers/registry.py`. This is an external HTTP API client and is not added
to `build_providers`.

HARD RULE: no method here issues PATCH, PUT, DELETE, or a comment/work-item-
update POST. The only two POSTs are the WIQL query and the work-item batch
fetch, both reads that happen to take a request body. A guard test greps this
file for the banned client calls.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx

from app.config import read_secret

API_VERSION = "7.1"
BATCH_LIMIT = 200


class AzureDevOpsError(Exception):
    """A DevOps API call failed, returned an unexpected body, or the PAT env
    var named by `secret_ref` is unset."""


class AzureDevOpsClient:
    """One org/project/team's read surface. A fresh `httpx.AsyncClient` is
    opened per call — this is a low-volume sync/poll client, not a hot path."""

    def __init__(
        self,
        *,
        org_url: str,
        project: str,
        secret_ref: str,
        team: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._org_url = org_url.rstrip("/")
        self._project = project
        self._secret_ref = secret_ref
        self._team = team
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        pat = read_secret(self._secret_ref)
        if not pat:
            raise AzureDevOpsError(f"{self._secret_ref} is not set")
        # DevOps takes an empty username and the PAT as the password.
        return httpx.AsyncClient(
            auth=httpx.BasicAuth("", pat), timeout=30, transport=self._transport
        )

    async def query_work_item_ids(self, wiql: str) -> list[int]:
        url = f"{self._org_url}/{self._project}/_apis/wit/wiql?api-version={API_VERSION}"
        async with self._client() as client:
            response = await client.post(url, json={"query": wiql})
        data = _read_json(response)
        items = data.get("workItems")
        if not isinstance(items, list):
            return []
        return [item["id"] for item in items if isinstance(item, dict) and "id" in item]

    async def get_work_items_batch(
        self, ids: Sequence[int], fields: Sequence[str]
    ) -> list[dict[str, Any]]:
        if not ids:
            return []
        url = f"{self._org_url}/_apis/wit/workitemsbatch?api-version={API_VERSION}"
        results: list[dict[str, Any]] = []
        for start in range(0, len(ids), BATCH_LIMIT):
            chunk = list(ids[start : start + BATCH_LIMIT])
            async with self._client() as client:
                response = await client.post(url, json={"ids": chunk, "fields": list(fields)})
            data = _read_json(response)
            values = data.get("value")
            if isinstance(values, list):
                results.extend(v for v in values if isinstance(v, dict))
        return results

    async def get_current_iteration_path(self) -> str | None:
        """The team's current iteration path, or None when no sprint has dates
        covering today. Uses the project's default team when none is set."""
        team = f"/{self._team}" if self._team else ""
        url = (
            f"{self._org_url}/{self._project}{team}/_apis/work/teamsettings/iterations"
            f"?$timeframe=current&api-version={API_VERSION}"
        )
        async with self._client() as client:
            response = await client.get(url)
        values = _read_values(response)
        path = values[0].get("path") if values else None
        return path if isinstance(path, str) and path else None

    async def get_authenticated_user_display_name(self) -> str | None:
        """The PAT owner's display name, or None when the org does not report one."""
        url = f"{self._org_url}/_apis/connectionData?api-version={API_VERSION}"
        async with self._client() as client:
            response = await client.get(url)
        user = _read_json(response).get("authenticatedUser")
        name = user.get("providerDisplayName") if isinstance(user, dict) else None
        return name if isinstance(name, str) and name else None

    async def list_active_prs(self) -> list[dict[str, Any]]:
        url = (
            f"{self._org_url}/{self._project}/_apis/git/pullrequests"
            f"?searchCriteria.status=active&api-version={API_VERSION}"
        )
        async with self._client() as client:
            response = await client.get(url)
        return _read_values(response)

    async def list_pr_threads(self, repository_id: str, pr_id: int) -> list[dict[str, Any]]:
        url = (
            f"{self._org_url}/{self._project}/_apis/git/repositories/{repository_id}"
            f"/pullRequests/{pr_id}/threads?api-version={API_VERSION}"
        )
        async with self._client() as client:
            response = await client.get(url)
        return _read_values(response)


def _read_json(response: httpx.Response) -> dict[str, Any]:
    if response.status_code >= 300:
        raise AzureDevOpsError(f"DevOps API {response.status_code}: {response.text[:500]}")
    try:
        data = response.json()
    except ValueError as exc:
        raise AzureDevOpsError("DevOps API returned a non-JSON body") from exc
    if not isinstance(data, dict):
        raise AzureDevOpsError("DevOps API returned an unexpected body shape")
    return data


def _read_values(response: httpx.Response) -> list[dict[str, Any]]:
    values = _read_json(response).get("value")
    return [v for v in values if isinstance(v, dict)] if isinstance(values, list) else []
