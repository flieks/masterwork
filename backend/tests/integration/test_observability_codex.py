"""Codex wiring against a temp `hooks.json`.

Every test points the integration at a throwaway Codex home — nothing here may
ever touch the real `~/.codex`. Mirrors the Claude Code suite where the two
behave the same, and covers what only Codex has: the `[features] hooks`
switch in `config.toml`, and a second card next to Claude Code's.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

from app.api.deps import get_integrations
from app.main import app
from app.observability.claude_code import ClaudeCodeIntegration
from app.observability.codex import EVENTS, CodexIntegration
from app.observability.registry import FORWARDERS

URL = "/api/v1/observability/integrations"
INGEST = "http://localhost:8008/api/v1/hooks/events"

FOREIGN_HOOK = {"type": "command", "command": "/usr/local/bin/my-own-hook.sh"}


@pytest.fixture
def codex_home(tmp_path: Path) -> Path:
    home = tmp_path / "codex"
    home.mkdir()
    return home


def _integration(codex_home: Path, tmp_path: Path) -> CodexIntegration:
    return CodexIntegration(
        settings_path=codex_home / "hooks.json",
        hooks_dir=tmp_path / "masterwork" / "hooks",
        forwarder=FORWARDERS / "codex.py",
        ingest_url=INGEST,
        media_dir=tmp_path / "masterwork" / "media",
        config_toml=codex_home / "config.toml",
    )


@pytest.fixture
def wire(client: AsyncClient, codex_home: Path, tmp_path: Path) -> Path:
    """Register a Codex integration on temp paths; returns hooks.json."""
    integration = _integration(codex_home, tmp_path)
    app.dependency_overrides[get_integrations] = lambda: [integration]
    return codex_home / "hooks.json"


def read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def commands(settings: dict[str, Any], event: str) -> list[str]:
    return [
        hook["command"]
        for group in settings.get("hooks", {}).get(event, [])
        for hook in group.get("hooks", [])
    ]


async def test_lists_codex_as_disconnected(client: AsyncClient, wire: Path) -> None:
    r = await client.get(URL)
    assert r.status_code == 200
    (integration,) = r.json()
    assert integration["id"] == "codex"
    assert integration["label"] == "Codex"
    assert integration["state"] == "disconnected"
    assert "Codex isn't reporting" in integration["detail"]
    assert integration["events"] == EVENTS
    assert integration["config_path"] == str(wire)


async def test_both_agents_are_listed_as_their_own_card(
    client: AsyncClient, codex_home: Path, tmp_path: Path
) -> None:
    claude_home = tmp_path / "claude"
    claude_home.mkdir()
    claude = ClaudeCodeIntegration(
        settings_path=claude_home / "settings.json",
        hooks_dir=tmp_path / "masterwork" / "hooks",
        forwarder=FORWARDERS / "claude_code.py",
        ingest_url=INGEST,
        media_dir=tmp_path / "masterwork" / "media",
    )
    app.dependency_overrides[get_integrations] = lambda: [
        claude,
        _integration(codex_home, tmp_path),
    ]

    r = await client.get(URL)
    assert [i["id"] for i in r.json()] == ["claude-code", "codex"]

    # Connecting one leaves the other exactly where it was.
    r = await client.post(f"{URL}/codex/connect")
    assert r.json()["state"] == "connected"
    r = await client.get(URL)
    assert [(i["id"], i["state"]) for i in r.json()] == [
        ("claude-code", "disconnected"),
        ("codex", "connected"),
    ]
    assert not (claude_home / "settings.json").exists()


async def test_connect_installs_hooks_and_the_forwarder(client: AsyncClient, wire: Path) -> None:
    r = await client.post(f"{URL}/codex/connect")
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "connected"

    settings = read(wire)
    assert sorted(settings["hooks"]) == sorted(EVENTS)
    for event in EVENTS:
        assert len(commands(settings, event)) == 1

    # Its own forwarder, not Claude Code's, and a sidecar naming the ingest.
    script = Path(body["script_path"])
    assert script.name == "codex.py"
    assert script.read_bytes() == (FORWARDERS / "codex.py").read_bytes()
    assert commands(settings, "SessionStart")[0].endswith(str(script))
    assert json.loads((script.parent / "config.json").read_text())["ingest_url"] == INGEST

    # No spawn tool to match on, so no PreToolUse at all — Codex says
    # SubagentStart itself. SessionEnd is the one hook that must finish.
    assert "PreToolUse" not in settings["hooks"]
    assert "SubagentStart" in settings["hooks"]
    assert settings["hooks"]["SessionEnd"][0]["hooks"][0].get("async") is None
    assert settings["hooks"]["Stop"][0]["hooks"][0]["async"] is True
    assert all("matcher" not in group for groups in settings["hooks"].values() for group in groups)


async def test_connect_is_idempotent(client: AsyncClient, wire: Path) -> None:
    await client.post(f"{URL}/codex/connect")
    first = read(wire)
    r = await client.post(f"{URL}/codex/connect")
    assert r.json()["state"] == "connected"
    assert read(wire) == first


async def test_connect_keeps_hooks_it_does_not_own(client: AsyncClient, wire: Path) -> None:
    wire.write_text(
        json.dumps(
            {
                "description": "mine",
                "hooks": {
                    "Stop": [{"hooks": [FOREIGN_HOOK]}],
                    "PreCompact": [{"matcher": "auto", "hooks": [FOREIGN_HOOK]}],
                },
            }
        ),
        encoding="utf-8",
    )

    r = await client.post(f"{URL}/codex/connect")
    assert r.status_code == 200

    settings = read(wire)
    assert settings["description"] == "mine"
    assert FOREIGN_HOOK["command"] in commands(settings, "Stop")
    assert len(commands(settings, "Stop")) == 2
    assert settings["hooks"]["PreCompact"] == [{"matcher": "auto", "hooks": [FOREIGN_HOOK]}]


async def test_connect_backs_the_config_up_before_rewriting(
    client: AsyncClient, wire: Path
) -> None:
    original = json.dumps({"description": "mine"})
    wire.write_text(original, encoding="utf-8")

    r = await client.post(f"{URL}/codex/connect")

    backup = r.json()["backup_path"]
    assert backup == str(wire) + ".masterwork.bak"
    assert Path(backup).read_text(encoding="utf-8") == original


async def test_a_missing_forwarder_reads_as_outdated_and_connect_repairs_it(
    client: AsyncClient, wire: Path
) -> None:
    r = await client.post(f"{URL}/codex/connect")
    Path(r.json()["script_path"]).unlink()

    r = await client.get(URL)
    assert r.json()[0]["state"] == "outdated"

    r = await client.post(f"{URL}/codex/connect")
    assert r.json()["state"] == "connected"


async def test_disconnect_removes_only_our_hooks(client: AsyncClient, wire: Path) -> None:
    wire.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [FOREIGN_HOOK]}]}}), encoding="utf-8")
    await client.post(f"{URL}/codex/connect")

    r = await client.post(f"{URL}/codex/disconnect")
    assert r.json()["state"] == "disconnected"

    settings = read(wire)
    assert commands(settings, "Stop") == [FOREIGN_HOOK["command"]]
    assert "SessionStart" not in settings["hooks"]


async def test_disconnect_leaves_a_never_connected_config_alone(
    client: AsyncClient, wire: Path
) -> None:
    r = await client.post(f"{URL}/codex/disconnect")
    assert r.status_code == 200
    assert not wire.exists()


async def test_unreadable_config_is_reported_not_overwritten(
    client: AsyncClient, wire: Path
) -> None:
    wire.write_text("{ this is not json", encoding="utf-8")

    r = await client.get(URL)
    assert r.json()[0]["state"] == "unavailable"
    assert "not valid JSON" in r.json()[0]["detail"]

    r = await client.post(f"{URL}/codex/connect")
    assert r.status_code == 409
    assert wire.read_text(encoding="utf-8") == "{ this is not json"


async def test_unavailable_when_codex_never_ran_here(client: AsyncClient, tmp_path: Path) -> None:
    missing = tmp_path / "no-codex-here"
    app.dependency_overrides[get_integrations] = lambda: [_integration(missing, tmp_path)]

    r = await client.get(URL)
    assert r.json()[0]["state"] == "unavailable"
    assert "Codex hasn't run on this machine yet" in r.json()[0]["detail"]

    r = await client.post(f"{URL}/codex/connect")
    assert r.status_code == 409
    assert not missing.exists()


async def test_hooks_switched_off_in_config_toml_block_connecting(
    client: AsyncClient, wire: Path, codex_home: Path
) -> None:
    """Hooks are on by default; only an explicit `false` makes what we would
    write a no-op, and writing it anyway would read as connected while
    recording nothing."""
    (codex_home / "config.toml").write_text(
        'model = "gpt-5-codex"\n\n[features]\nhooks = false\n', encoding="utf-8"
    )

    r = await client.get(URL)
    assert r.json()[0]["state"] == "unavailable"
    assert "hooks = false" in r.json()[0]["detail"]

    r = await client.post(f"{URL}/codex/connect")
    assert r.status_code == 409
    assert not wire.exists()


async def test_config_toml_that_leaves_hooks_alone_is_no_blocker(
    client: AsyncClient, wire: Path, codex_home: Path
) -> None:
    (codex_home / "config.toml").write_text(
        'model = "gpt-5-codex"\n\n[features]\nweb_search = true\n', encoding="utf-8"
    )
    r = await client.post(f"{URL}/codex/connect")
    assert r.json()["state"] == "connected"


async def test_a_config_toml_that_will_not_parse_is_codexs_problem_not_ours(
    client: AsyncClient, wire: Path, codex_home: Path
) -> None:
    (codex_home / "config.toml").write_text("[features\nhooks = false\n", encoding="utf-8")
    r = await client.post(f"{URL}/codex/connect")
    assert r.json()["state"] == "connected"
    # Only hooks.json was ever written.
    assert (codex_home / "config.toml").read_text(encoding="utf-8") == "[features\nhooks = false\n"
