"""Envelope attempts and per-check gate notes: ingest, read, and what a replay
can still prove. Against the real test database, never a mock."""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient


async def _ingest(client: AsyncClient, **body: Any) -> None:
    r = await client.post("/api/v1/hooks/events", json=body)
    assert r.status_code == 204, r.text


async def _detail(client: AsyncClient, session_id: str = "s1") -> dict[str, Any]:
    r = await client.get(f"/api/v1/coding-sessions/{session_id}")
    assert r.status_code == 200, r.text
    return r.json()


def _factory_gate(phase: str, gate: str, detail: str, *, ok: bool) -> dict[str, Any]:
    """A pre-v1.19 `gate_pass`/`gate_fail` exactly as the runner posts one."""
    event = "gate_pass" if ok else "gate_fail"
    return {
        "session_id": "s1",
        "event_type": event,
        "cwd": "/tmp/repo",
        "payload": {
            "phase": phase,
            "event": event,
            "agent": phase,
            "result": "ok" if ok else "fail",
            "detail": f"{gate}: {detail}",
            "payload": {"gate": gate},
        },
    }


async def _open_phase(client: AsyncClient, name: str = "build") -> None:
    await _ingest(
        client,
        session_id="s1",
        event_type="phase_start",
        cwd="/tmp/repo",
        phase={"name": name, "seq": 1, "kind": "agent", "status": "running"},
        agent={"name": name},
    )


# --- both blocks ingesting --------------------------------------------------


async def test_envelope_and_gate_blocks_are_stored(client: AsyncClient) -> None:
    await _open_phase(client)
    body = {
        "status": "ok",
        "summary": "Reviewed the diff.",
        "approved": True,
        "changed_files": ["README.md", "strings.py"],
    }
    await _ingest(
        client,
        session_id="s1",
        event_type="gate_fail",
        phase={"name": "build", "seq": 1},
        agent={"name": "build"},
        envelope={"role": "review", "parsed": True, "status": "ok", "body": body},
        gate={
            "name": "changed_files",
            "note": "claimed but not changed on disk: README.md, strings.py",
        },
    )

    detail = await _detail(client)
    (envelope,) = detail["envelopes"]
    assert envelope["role"] == "review"
    assert envelope["parsed"] is True
    assert envelope["status"] == "ok"
    assert envelope["body"] == body  # verbatim, not a summary of it
    assert envelope["origin"] == "reported"
    assert envelope["attempt"] == 1

    (check,) = detail["gate_checks"]
    assert check["gate"] == "changed_files"
    assert check["ok"] is False  # read off the gate_fail it rode in on
    assert check["note"] == "claimed but not changed on disk: README.md, strings.py"
    assert check["origin"] == "reported"
    assert check["item"] is None
    # Both point at the stage, so a phase can show its own evidence.
    assert envelope["phase_id"] == check["phase_id"] == detail["phases"][0]["id"]


async def test_gate_block_writes_one_row_per_check(client: AsyncClient) -> None:
    await _open_phase(client)
    await _ingest(
        client,
        session_id="s1",
        event_type="gate_fail",
        phase={"name": "build", "seq": 1},
        gate={
            "name": "checks",
            "ok": False,
            "checks": [
                {"item": "pytest", "ok": True, "note": "436 passed"},
                {"item": "mypy", "ok": False, "note": "app/x.py:3: error: bad"},
            ],
        },
    )

    checks = (await _detail(client))["gate_checks"]
    assert [(c["item"], c["ok"], c["note"]) for c in checks] == [
        ("pytest", True, "436 passed"),
        ("mypy", False, "app/x.py:3: error: bad"),
    ]
    assert {c["gate"] for c in checks} == {"checks"}


async def test_rejected_envelope_keeps_the_reply_it_could_not_read(client: AsyncClient) -> None:
    await _open_phase(client)
    await _ingest(
        client,
        session_id="s1",
        event_type="gate_fail",
        phase={"name": "build", "seq": 1},
        envelope={
            "role": "build",
            "parse_error": "the last fenced block is `text`, not `json`",
            "raw_text": "I changed two files.\n```text\nnot json\n```",
        },
        gate={"name": "envelope", "note": "the last fenced block is `text`, not `json`"},
    )

    (envelope,) = (await _detail(client))["envelopes"]
    assert envelope["parsed"] is False  # inferred from parse_error alone
    assert envelope["body"] is None
    assert envelope["raw_text"].endswith("```")
    assert envelope["parse_error"] == "the last fenced block is `text`, not `json`"


async def test_envelope_role_falls_back_to_the_lane(client: AsyncClient) -> None:
    await _ingest(
        client,
        session_id="s1",
        event_type="phase_end",
        phase={"name": "review", "seq": 1},
        agent={"name": "review"},
        envelope={"parsed": True, "status": "blocked", "body": {"status": "blocked"}},
    )
    (envelope,) = (await _detail(client))["envelopes"]
    assert envelope["role"] == "review"


# --- v1.18-shaped bodies keep working --------------------------------------


async def test_v118_body_is_untouched(client: AsyncClient) -> None:
    """A producer that sends neither block behaves exactly as before."""
    await _ingest(
        client,
        session_id="s1",
        event_type="phase_start",
        cwd="/tmp/repo",
        model="opus",
        title="Ship the evidence tables",
        workflow="factory",
        phase={"name": "build", "seq": 1, "kind": "agent", "status": "running"},
        agent={"name": "build", "model": "opus", "context_window": 200_000},
        ok=True,
        duration_ms=120,
    )
    await _ingest(
        client,
        session_id="s1",
        event_type="phase_end",
        phase={"name": "build", "seq": 1, "status": "passed", "cost_usd": 0.5},
    )

    detail = await _detail(client)
    assert detail["title"] == "Ship the evidence tables"
    assert detail["workflow"] == "factory"
    assert [p["status"] for p in detail["phases"]] == ["passed"]
    assert detail["agents"][0]["name"] == "build"
    assert detail["envelopes"] == []
    assert detail["gate_checks"] == []


async def test_list_endpoint_does_not_ship_evidence(client: AsyncClient) -> None:
    await _open_phase(client)
    await _ingest(
        client,
        session_id="s1",
        event_type="gate_pass",
        phase={"name": "build", "seq": 1},
        envelope={"parsed": True, "body": {"status": "ok", "summary": "x" * 500}},
        gate={"name": "envelope", "note": "parsed a valid build envelope"},
    )

    (card,) = (await client.get("/api/v1/coding-sessions")).json()
    assert "envelopes" not in card
    assert "gate_checks" not in card


# --- malformed evidence is dropped, never 422 -------------------------------


async def test_malformed_blocks_are_dropped_not_rejected(client: AsyncClient) -> None:
    # `phase_end` so nothing is recoverable either — this asserts the drop, not
    # the fallback.
    for bad in (
        {"envelope": "not an object"},
        {"envelope": {"attempt": "two", "body": {"status": "ok"}}},
        {"envelope": {"body": "a string, not an object"}},
        {"gate": 5},
        {"gate": {"name": "checks", "checks": "not a list"}},
        {"gate": {"name": "checks", "checks": [{"ok": "maybe"}]}},
    ):
        await _ingest(client, session_id="s1", event_type="phase_end", **bad)

    detail = await _detail(client)
    assert detail["envelopes"] == []
    assert detail["gate_checks"] == []
    assert detail["event_count"] == 6  # every one of them was still recorded


async def test_empty_blocks_record_nothing(client: AsyncClient) -> None:
    await _ingest(client, session_id="s1", event_type="phase_end", envelope={}, gate={})
    detail = await _detail(client)
    assert detail["envelopes"] == []
    assert detail["gate_checks"] == []


async def test_a_dropped_gate_block_still_falls_back_to_the_event(client: AsyncClient) -> None:
    """The block is unusable, so the event is mined instead — one or the other,
    never neither."""
    await _ingest(
        client,
        session_id="s1",
        event_type="gate_fail",
        gate={"name": "boundary", "checks": "not a list"},
        payload={"detail": "boundary: wrote outside", "payload": {"gate": "boundary"}},
    )
    (check,) = (await _detail(client))["gate_checks"]
    assert (check["gate"], check["ok"], check["origin"]) == ("boundary", False, "recovered")
    assert check["note"] == "wrote outside"


async def test_oversized_note_is_truncated_with_a_marker(client: AsyncClient) -> None:
    await _ingest(
        client,
        session_id="s1",
        event_type="gate_fail",
        gate={"name": "checks", "note": "x" * 9_000},
    )
    (check,) = (await _detail(client))["gate_checks"]
    assert check["note"].startswith("x" * 8_000)
    assert "truncated, 9000 chars" in check["note"]


# --- attempts on one phase, in order ---------------------------------------


async def test_attempts_are_counted_and_ordered(client: AsyncClient) -> None:
    await _open_phase(client)
    for note in ("first try", "second try", "third try"):
        await _ingest(
            client,
            session_id="s1",
            event_type="gate_fail",
            phase={"name": "build", "seq": 1},
            envelope={"role": "build", "parsed": True, "body": {"summary": note}},
            gate={"name": "changed_files", "note": note},
        )

    detail = await _detail(client)
    assert [e["attempt"] for e in detail["envelopes"]] == [1, 2, 3]
    assert [e["body"]["summary"] for e in detail["envelopes"]] == [
        "first try",
        "second try",
        "third try",
    ]
    assert [(c["attempt"], c["note"]) for c in detail["gate_checks"]] == [
        (1, "first try"),
        (2, "second try"),
        (3, "third try"),
    ]


async def test_attempts_are_counted_per_phase_and_per_item(client: AsyncClient) -> None:
    for seq, phase in enumerate(("plan", "build"), start=1):
        await _ingest(
            client, session_id="s1", event_type="phase_start", phase={"name": phase, "seq": seq}
        )
        for _ in range(2):
            await _ingest(
                client,
                session_id="s1",
                event_type="gate_pass",
                phase={"name": phase, "seq": seq},
                gate={"name": "checks", "item": "pytest", "note": "passed"},
            )
            await _ingest(
                client,
                session_id="s1",
                event_type="gate_pass",
                phase={"name": phase, "seq": seq},
                gate={"name": "checks", "item": "mypy", "note": "clean"},
            )

    checks = (await _detail(client))["gate_checks"]
    by_phase: dict[int, list[tuple[str, int]]] = {}
    for check in checks:
        by_phase.setdefault(check["phase_id"], []).append((check["item"], check["attempt"]))
    assert len(by_phase) == 2
    for entries in by_phase.values():
        assert entries == [("pytest", 1), ("mypy", 1), ("pytest", 2), ("mypy", 2)]


async def test_a_stated_attempt_wins_over_the_count(client: AsyncClient) -> None:
    await _open_phase(client)
    await _ingest(
        client,
        session_id="s1",
        event_type="gate_fail",
        phase={"name": "build", "seq": 1},
        gate={"name": "verdict", "attempt": 4, "note": "approved: true with 2 blocking"},
    )
    (check,) = (await _detail(client))["gate_checks"]
    assert check["attempt"] == 4


# --- what a replay can recover ---------------------------------------------


async def test_history_recovers_gate_name_verdict_and_note(client: AsyncClient) -> None:
    """The pre-v1.19 stream carries all three, so a live ingest of an old-shaped
    event records them too — the backfill is the same code path."""
    await _ingest(
        client,
        session_id="s1",
        event_type="phase_start",
        cwd="/tmp/repo",
        payload={"phase": "build", "event": "phase_start", "agent": "build", "detail": ""},
    )
    await _ingest(
        client,
        **_factory_gate(
            "build", "changed_files", "claimed but not changed on disk: README.md", ok=False
        ),
    )

    detail = await _detail(client)
    (check,) = detail["gate_checks"]
    assert check["gate"] == "changed_files"
    assert check["ok"] is False
    # The `"<gate>: "` prefix the runner writes is undone; the note is the note.
    assert check["note"] == "claimed but not changed on disk: README.md"
    assert check["origin"] == "recovered"
    assert check["phase_id"] == detail["phases"][0]["id"]


async def test_history_recovers_the_envelope_attempt_but_never_its_body(
    client: AsyncClient,
) -> None:
    await _ingest(
        client,
        **_factory_gate("build", "envelope", "no fenced code block found in the reply", ok=False),
    )
    await _ingest(client, **_factory_gate("build", "envelope", "parsed a valid build", ok=True))

    envelopes = (await _detail(client))["envelopes"]
    assert [(e["attempt"], e["parsed"], e["origin"]) for e in envelopes] == [
        (1, False, "recovered"),
        (2, True, "recovered"),
    ]
    assert envelopes[0]["parse_error"] == "no fenced code block found in the reply"
    # Never posted, never invented.
    assert [e["body"] for e in envelopes] == [None, None]
    assert [e["raw_text"] for e in envelopes] == [None, None]
    assert [e["status"] for e in envelopes] == [None, None]


async def test_a_verdict_that_named_no_gate_is_filed_under_stage(client: AsyncClient) -> None:
    await _ingest(
        client,
        session_id="s1",
        event_type="gate_fail",
        cwd="/tmp/repo",
        payload={
            "phase": "review",
            "event": "gate_fail",
            "agent": "review",
            "result": "fail",
            "detail": "stage returned status=blocked: cannot review an empty diff",
            "payload": {},
        },
    )
    (check,) = (await _detail(client))["gate_checks"]
    assert check["gate"] == "stage"
    assert check["note"] == "stage returned status=blocked: cannot review an empty diff"


# --- backfill ---------------------------------------------------------------


async def test_backfill_is_idempotent(client: AsyncClient) -> None:
    await _ingest(
        client,
        session_id="s1",
        event_type="phase_start",
        cwd="/tmp/repo",
        payload={"phase": "build", "event": "phase_start", "agent": "build", "detail": ""},
    )
    for gate, note, ok in (
        ("envelope", "parsed a valid build envelope", True),
        ("changed_files", "claimed but not changed on disk: README.md", False),
        ("changed_files", "2 file(s) match the claim", True),
    ):
        await _ingest(client, **_factory_gate("build", gate, note, ok=ok))

    before = await _detail(client)
    assert len(before["gate_checks"]) == 3
    assert len(before["envelopes"]) == 1

    for _ in range(2):
        r = await client.post("/api/v1/coding-sessions/s1/backfill")
        assert r.status_code == 200, r.text
        assert r.json()["gate_checks"] == 3
        assert r.json()["envelopes"] == 1

    after = await _detail(client)
    assert [(c["gate"], c["attempt"], c["ok"], c["note"]) for c in after["gate_checks"]] == [
        (c["gate"], c["attempt"], c["ok"], c["note"]) for c in before["gate_checks"]
    ]
    # Phases are dropped and rebuilt, so the ids move — but every row still
    # points at the stage it belongs to.
    assert {c["phase_id"] for c in after["gate_checks"]} == {after["phases"][0]["id"]}


async def test_backfill_preserves_a_reported_envelope_body(client: AsyncClient) -> None:
    """The hook body is not stored, so a rebuild cannot recreate this row — it
    has to keep it, and re-point it at the stage the replay rebuilt."""
    await _ingest(
        client,
        session_id="s1",
        event_type="phase_start",
        cwd="/tmp/repo",
        payload={"phase": "build", "event": "phase_start", "agent": "build", "detail": ""},
    )
    body = {"status": "ok", "summary": "Built it.", "changed_files": ["a.py"]}
    await _ingest(
        client,
        **_factory_gate("build", "envelope", "parsed a valid build envelope", ok=True),
        envelope={"role": "build", "parsed": True, "status": "ok", "body": body},
        gate={"name": "envelope", "note": "parsed a valid build envelope"},
    )
    old_phase_id = (await _detail(client))["phases"][0]["id"]

    r = await client.post("/api/v1/coding-sessions/s1/backfill")
    assert r.json()["envelopes"] == 1
    assert r.json()["gate_checks"] == 1

    after = await _detail(client)
    (envelope,) = after["envelopes"]
    assert envelope["body"] == body
    assert envelope["origin"] == "reported"
    # Re-pointed, not orphaned: the stage row is a new one.
    assert after["phases"][0]["id"] != old_phase_id
    assert envelope["phase_id"] == after["phases"][0]["id"]
    # And the reported gate check was not doubled by a recovered twin.
    (check,) = after["gate_checks"]
    assert check["origin"] == "reported"


async def test_reported_evidence_outlives_a_stage_the_replay_cannot_rebuild(
    client: AsyncClient,
) -> None:
    """A producer that names its stage only in the hook body loses the stage on
    a replay — long-standing behaviour. The evidence survives with an honest
    null link rather than a `phase_id` pointing at a deleted row."""
    await _open_phase(client)
    await _ingest(
        client,
        session_id="s1",
        event_type="gate_fail",
        phase={"name": "build", "seq": 1},
        gate={"name": "artifacts", "note": "plan.md (declared but does not exist)"},
    )

    await client.post("/api/v1/coding-sessions/s1/backfill")
    after = await _detail(client)
    assert after["phases"] == []
    (check,) = after["gate_checks"]
    assert check["phase_id"] is None
    assert check["note"] == "plan.md (declared but does not exist)"


async def test_backfill_all_reports_evidence_totals(client: AsyncClient) -> None:
    await _ingest(
        client, **_factory_gate("build", "boundary", "all writes inside [app/**]", ok=True)
    )
    await _ingest(client, **_factory_gate("build", "artifacts", "1 artifact(s) present", ok=True))

    r = await client.post("/api/v1/coding-sessions/backfill")
    assert r.status_code == 200, r.text
    totals = r.json()
    assert totals["sessions"] == 1
    assert totals["gate_checks"] == 2
    assert totals["envelopes"] == 0


async def test_backfill_of_a_run_with_no_evidence_reports_zero(client: AsyncClient) -> None:
    await _ingest(client, session_id="s1", event_type="SessionStart", cwd="/tmp/repo")
    await _ingest(client, session_id="s1", event_type="PostToolUse", tool_name="Bash")

    r = await client.post("/api/v1/coding-sessions/s1/backfill")
    assert (r.json()["envelopes"], r.json()["gate_checks"]) == (0, 0)
