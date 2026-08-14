"""AzureDevOpsClient against httpx.MockTransport — no test ever reaches the network."""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable

import httpx
import pytest

from app.providers import azuredevops
from app.providers.azuredevops import AzureDevOpsClient, AzureDevOpsError

SECRET_REF = "TEST_DEVOPS_PAT"


@pytest.fixture(autouse=True)
def _pat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SECRET_REF, "s3cr3t")


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> AzureDevOpsClient:
    return AzureDevOpsClient(
        org_url="https://dev.azure.com/acme",
        project="widgets",
        secret_ref=SECRET_REF,
        transport=httpx.MockTransport(handler),
    )


async def test_query_work_item_ids_posts_wiql_to_the_documented_url() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"workItems": [{"id": 1}, {"id": 2}]})

    ids = await _client(handler).query_work_item_ids("SELECT [System.Id] FROM WorkItems")

    assert ids == [1, 2]
    assert captured["url"] == "https://dev.azure.com/acme/widgets/_apis/wit/wiql?api-version=7.1"
    assert captured["body"] == {"query": "SELECT [System.Id] FROM WorkItems"}


async def test_batch_chunks_at_200_and_unions_the_results() -> None:
    calls: list[list[int]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == (
            "https://dev.azure.com/acme/_apis/wit/workitemsbatch?api-version=7.1"
        )
        body = json.loads(request.content)
        calls.append(body["ids"])
        return httpx.Response(200, json={"value": [{"id": i} for i in body["ids"]]})

    result = await _client(handler).get_work_items_batch(list(range(1, 251)), ["System.Title"])

    assert [len(c) for c in calls] == [200, 50]
    assert {item["id"] for item in result} == set(range(1, 251))


async def test_batch_with_no_ids_makes_no_requests() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request should have been made")

    result = await _client(handler).get_work_items_batch([], ["System.Title"])

    assert result == []


async def test_missing_pat_raises_and_makes_no_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(SECRET_REF, raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request should have been made")

    with pytest.raises(AzureDevOpsError, match=SECRET_REF):
        await _client(handler).query_work_item_ids("SELECT [System.Id] FROM WorkItems")


async def test_non_2xx_response_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="service unavailable")

    with pytest.raises(AzureDevOpsError):
        await _client(handler).query_work_item_ids("SELECT [System.Id] FROM WorkItems")


async def test_pr_endpoints_hit_the_documented_urls() -> None:
    captured: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(str(request.url))
        return httpx.Response(200, json={"value": []})

    client = _client(handler)
    await client.list_active_prs()
    await client.list_pr_threads("repo-1", 42)

    assert captured[0] == (
        "https://dev.azure.com/acme/widgets/_apis/git/pullrequests"
        "?searchCriteria.status=active&api-version=7.1"
    )
    assert captured[1] == (
        "https://dev.azure.com/acme/widgets/_apis/git/repositories/repo-1"
        "/pullRequests/42/threads?api-version=7.1"
    )


def test_module_has_no_write_methods() -> None:
    """The hard rule, made mechanical: no PATCH/PUT/DELETE call anywhere in this file."""
    source = inspect.getsource(azuredevops)
    for banned in (".patch(", ".put(", ".delete("):
        assert banned not in source
