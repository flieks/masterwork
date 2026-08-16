"""The context-growth series: ingest, read, survival across a backfill.

Against the real test database, never a mock — same shape as test_coding_evidence.py.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient


async def _ingest(client: AsyncClient, **body: Any) -> None:
    r = await client.post("/api/v1/hooks/events", json=body)
    assert r.status_code == 204, r.text


async def _context(client: AsyncClient, session_id: str = "s1") -> dict[str, Any]:
    r = await client.get(f"/api/v1/coding-sessions/{session_id}/context")
    assert r.status_code == 200, r.text
    return r.json()


def _sample(
    seq: int,
    message_id: str,
    total_tokens: int,
    *,
    is_sidechain: bool = False,
    tools: list[str] | None = None,
    at: str = "2026-08-15T00:00:00Z",
) -> dict[str, Any]:
    return {
        "seq": seq,
        "message_id": message_id,
        "at": at,
        "total_tokens": total_tokens,
        "output_tokens": 10,
        "is_sidechain": is_sidechain,
        "tools": tools,
    }


async def _stop(client: AsyncClient, samples: list[dict[str, Any]], session_id: str = "s1") -> None:
    await _ingest(client, session_id=session_id, event_type="Stop", context_samples=samples)


async def test_samples_come_back_in_seq_order_with_delta_and_header(
    client: AsyncClient,
) -> None:
    await _stop(
        client,
        [
            _sample(1, "msg_1", 1000, tools=["Read"]),
            _sample(2, "msg_2", 1500, tools=["Edit"]),
            _sample(3, "msg_3", 1800, tools=["Read", "Edit"]),
        ],
    )
    series = await _context(client)
    assert [s["message_id"] for s in series["samples"]] == ["msg_1", "msg_2", "msg_3"]
    assert [s["delta_tokens"] for s in series["samples"]] == [None, 500, 300]
    assert series["baseline_tokens"] == 1000
    assert series["peak_tokens"] == 1800
    assert series["sidechain_samples"] == []


async def test_a_session_with_no_samples_returns_an_empty_series(client: AsyncClient) -> None:
    await _ingest(client, session_id="s1", event_type="SessionStart", cwd="/tmp/repo")
    series = await _context(client)
    assert series["samples"] == []
    assert series["sidechain_samples"] == []
    assert series["baseline_tokens"] is None
    assert series["peak_tokens"] is None
    assert series["tools"] == []


async def test_an_unknown_session_404s(client: AsyncClient) -> None:
    r = await client.get("/api/v1/coding-sessions/nope/context")
    assert r.status_code == 404


async def test_samples_survive_a_backfill(client: AsyncClient) -> None:
    await _stop(
        client,
        [
            _sample(1, "msg_1", 1000, tools=["Read"]),
            _sample(2, "msg_2", 1500, tools=["Edit"]),
        ],
    )
    before = await _context(client)

    r = await client.post("/api/v1/coding-sessions/s1/backfill")
    assert r.status_code == 200, r.text

    after = await _context(client)
    assert after == before


async def test_reposting_the_same_cumulative_list_is_idempotent(client: AsyncClient) -> None:
    samples = [
        _sample(1, "msg_1", 1000, tools=["Read"]),
        _sample(2, "msg_2", 1500, tools=["Edit"]),
    ]
    await _stop(client, samples)
    await _stop(client, samples)

    series = await _context(client)
    assert [s["message_id"] for s in series["samples"]] == ["msg_1", "msg_2"]
    assert [s["delta_tokens"] for s in series["samples"]] == [None, 500]


async def test_rollup_ranks_splits_a_shared_sample_and_excludes_a_truncation(
    client: AsyncClient,
) -> None:
    await _stop(
        client,
        [
            _sample(1, "msg_1", 1000),
            # +1000, split 500/500 across Read and Edit.
            _sample(2, "msg_2", 2000, tools=["Read", "Edit"]),
            # +301, odd split: 151 to Read (first), 150 to Edit.
            _sample(3, "msg_3", 2301, tools=["Read", "Edit"]),
            # A context truncation: excluded from the rollup entirely.
            _sample(4, "msg_4", 1500, tools=["Read"]),
        ],
    )
    series = await _context(client)

    truncated = series["samples"][3]
    assert truncated["delta_tokens"] == -801
    assert truncated["is_truncation"] is True

    tools = {row["tool"]: row for row in series["tools"]}
    assert tools["Read"]["delta_tokens"] == 500 + 151
    assert tools["Edit"]["delta_tokens"] == 500 + 150
    assert tools["Read"]["calls"] == 2
    assert tools["Edit"]["calls"] == 2
    # Ranked by summed delta descending.
    assert [row["tool"] for row in series["tools"]] == ["Read", "Edit"]


async def test_sidechain_samples_are_their_own_series(client: AsyncClient) -> None:
    await _stop(
        client,
        [
            # First main sample: tools listed, but no predecessor — excluded
            # from the rollup, proving a sidechain leaking in isn't the reason.
            _sample(1, "msg_1", 1000, tools=["Task"]),
            _sample(2, "sub_1", 200, is_sidechain=True),
            _sample(3, "sub_2", 450, is_sidechain=True, tools=["Read"]),
            _sample(4, "msg_2", 1800, tools=["Edit"]),
            _sample(5, "msg_3", 2200, tools=["Task"]),
        ],
    )
    series = await _context(client)

    assert [s["message_id"] for s in series["samples"]] == ["msg_1", "msg_2", "msg_3"]
    assert [s["message_id"] for s in series["sidechain_samples"]] == ["sub_1", "sub_2"]
    # A sidechain sample's own lane-relative delta, not blended with main's.
    assert series["sidechain_samples"][0]["delta_tokens"] is None
    assert series["sidechain_samples"][1]["delta_tokens"] == 250
    # The header describes the main lane only.
    assert series["baseline_tokens"] == 1000
    assert series["peak_tokens"] == 2200
    # The sidechain's "Read" never leaks into the main-lane rollup.
    assert {row["tool"] for row in series["tools"]} == {"Task", "Edit"}
    assert [row["tool"] for row in series["tools"]] == ["Edit", "Task"]
