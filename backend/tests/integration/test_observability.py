"""Observability setup endpoints against a temp agent config.

Every test points the integration at a throwaway `settings.json` — nothing here
may ever touch the real `~/.claude`.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

from app.api.deps import get_integrations
from app.main import app
from app.observability.claude_code import EVENTS, ClaudeCodeIntegration
from app.observability.registry import FORWARDERS

URL = "/api/v1/observability/integrations"
INGEST = "http://localhost:8008/api/v1/hooks/events"

FOREIGN_HOOK = {"type": "command", "command": "/usr/local/bin/my-own-hook.sh"}


@pytest.fixture
def claude_home(tmp_path: Path) -> Path:
    home = tmp_path / "claude"
    home.mkdir()
    return home


@pytest.fixture
def wire(client: AsyncClient, claude_home: Path, tmp_path: Path) -> Path:
    """Register a Claude Code integration on temp paths; returns settings.json."""
    settings_path = claude_home / "settings.json"
    integration = ClaudeCodeIntegration(
        settings_path=settings_path,
        hooks_dir=tmp_path / "masterwork" / "hooks",
        forwarder=FORWARDERS / "claude_code.py",
        ingest_url=INGEST,
    )
    app.dependency_overrides[get_integrations] = lambda: [integration]
    return settings_path


def read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def commands(settings: dict[str, Any], event: str) -> list[str]:
    return [
        hook["command"]
        for group in settings.get("hooks", {}).get(event, [])
        for hook in group.get("hooks", [])
    ]


async def test_lists_claude_code_as_disconnected(client: AsyncClient, wire: Path) -> None:
    r = await client.get(URL)
    assert r.status_code == 200
    (integration,) = r.json()
    assert integration["id"] == "claude-code"
    assert integration["label"] == "Claude Code"
    assert integration["state"] == "disconnected"
    assert integration["ingest_url"] == INGEST
    assert integration["events"] == EVENTS
    assert integration["config_path"] == str(wire)


async def test_connect_installs_hooks_and_the_forwarder(client: AsyncClient, wire: Path) -> None:
    r = await client.post(f"{URL}/claude-code/connect")
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "connected"

    settings = read(wire)
    assert sorted(settings["hooks"]) == sorted(EVENTS)
    for event in EVENTS:
        assert len(commands(settings, event)) == 1

    # The command runs an absolute interpreter against the installed copy, so it
    # survives an npx cache prune.
    script = Path(body["script_path"])
    assert script.exists()
    assert commands(settings, "SessionStart")[0].endswith(str(script))
    assert json.loads((script.parent / "config.json").read_text())["ingest_url"] == INGEST

    # Only the subagent-spawn tool is matched on PreToolUse.
    assert settings["hooks"]["PreToolUse"][0]["matcher"] == "Task|Agent"
    # SessionEnd must finish before the process exits; everything else is async.
    assert settings["hooks"]["SessionEnd"][0]["hooks"][0].get("async") is None
    assert settings["hooks"]["Stop"][0]["hooks"][0]["async"] is True


async def test_a_path_with_a_space_stays_one_argument(
    client: AsyncClient, claude_home: Path, tmp_path: Path
) -> None:
    """The agent runs the command through a shell, and `/Users/Ada Lovelace/…`
    is a perfectly ordinary home directory."""
    integration = ClaudeCodeIntegration(
        settings_path=claude_home / "settings.json",
        hooks_dir=tmp_path / "Ada Lovelace" / "hooks",
        forwarder=FORWARDERS / "claude_code.py",
        ingest_url=INGEST,
    )
    app.dependency_overrides[get_integrations] = lambda: [integration]

    r = await client.post(f"{URL}/claude-code/connect")
    assert r.status_code == 200

    command = commands(read(claude_home / "settings.json"), "SessionStart")[0]
    assert shlex.split(command)[-1] == r.json()["script_path"]


async def test_connect_is_idempotent(client: AsyncClient, wire: Path) -> None:
    await client.post(f"{URL}/claude-code/connect")
    first = read(wire)
    r = await client.post(f"{URL}/claude-code/connect")

    assert r.status_code == 200
    assert r.json()["state"] == "connected"
    assert read(wire) == first


async def test_connect_keeps_hooks_it_does_not_own(client: AsyncClient, wire: Path) -> None:
    wire.write_text(
        json.dumps(
            {
                "model": "opus",
                "hooks": {
                    "Stop": [{"hooks": [FOREIGN_HOOK]}],
                    "PreCompact": [{"hooks": [FOREIGN_HOOK]}],
                },
            }
        ),
        encoding="utf-8",
    )

    r = await client.post(f"{URL}/claude-code/connect")
    assert r.status_code == 200

    settings = read(wire)
    assert settings["model"] == "opus"
    assert FOREIGN_HOOK["command"] in commands(settings, "Stop")
    assert len(commands(settings, "Stop")) == 2
    # An event we never subscribe to is left exactly as it was.
    assert commands(settings, "PreCompact") == [FOREIGN_HOOK["command"]]


async def test_connect_backs_the_config_up_before_rewriting(
    client: AsyncClient, wire: Path
) -> None:
    original = json.dumps({"model": "opus"})
    wire.write_text(original, encoding="utf-8")

    r = await client.post(f"{URL}/claude-code/connect")

    backup = r.json()["backup_path"]
    assert backup is not None
    assert Path(backup).read_text(encoding="utf-8") == original


async def test_stale_wiring_reads_as_outdated_and_connect_repairs_it(
    client: AsyncClient, wire: Path
) -> None:
    """A repo clone that ran the old installer points at a path that no longer
    exists — the state the first release would have left behind."""
    legacy = "python3 /gone/masterwork/scripts/claude-hook-observe.py"
    wire.write_text(
        json.dumps({"hooks": {event: [{"hooks": [{"command": legacy}]}] for event in EVENTS}}),
        encoding="utf-8",
    )

    r = await client.get(URL)
    assert r.json()[0]["state"] == "outdated"

    r = await client.post(f"{URL}/claude-code/connect")
    assert r.json()["state"] == "connected"
    settings = read(wire)
    for event in EVENTS:
        assert commands(settings, event) != [legacy]
        assert len(commands(settings, event)) == 1


async def test_disconnect_removes_only_our_hooks(client: AsyncClient, wire: Path) -> None:
    wire.write_text(
        json.dumps({"model": "opus", "hooks": {"Stop": [{"hooks": [FOREIGN_HOOK]}]}}),
        encoding="utf-8",
    )
    await client.post(f"{URL}/claude-code/connect")

    r = await client.post(f"{URL}/claude-code/disconnect")
    assert r.status_code == 200
    assert r.json()["state"] == "disconnected"

    settings = read(wire)
    assert settings["model"] == "opus"
    assert commands(settings, "Stop") == [FOREIGN_HOOK["command"]]
    # Events where we were the only subscriber are gone, not left empty.
    assert "SessionStart" not in settings["hooks"]


async def test_disconnect_leaves_a_never_connected_config_alone(
    client: AsyncClient, wire: Path
) -> None:
    r = await client.post(f"{URL}/claude-code/disconnect")
    assert r.status_code == 200
    assert not wire.exists()


async def test_unreadable_config_is_reported_not_overwritten(
    client: AsyncClient, wire: Path
) -> None:
    wire.write_text("{ this is not json", encoding="utf-8")

    r = await client.get(URL)
    body = r.json()[0]
    assert body["state"] == "unavailable"
    assert "not valid JSON" in body["detail"]

    r = await client.post(f"{URL}/claude-code/connect")
    assert r.status_code == 409
    assert wire.read_text(encoding="utf-8") == "{ this is not json"


async def test_unavailable_when_the_agent_never_ran_here(
    client: AsyncClient, tmp_path: Path
) -> None:
    missing = tmp_path / "no-claude-here"
    integration = ClaudeCodeIntegration(
        settings_path=missing / "settings.json",
        hooks_dir=tmp_path / "masterwork" / "hooks",
        forwarder=FORWARDERS / "claude_code.py",
        ingest_url=INGEST,
    )
    app.dependency_overrides[get_integrations] = lambda: [integration]

    r = await client.get(URL)
    assert r.json()[0]["state"] == "unavailable"

    r = await client.post(f"{URL}/claude-code/connect")
    assert r.status_code == 409
    assert not missing.exists()


async def test_unknown_integration_is_404(client: AsyncClient, wire: Path) -> None:
    r = await client.post(f"{URL}/codex/connect")
    assert r.status_code == 404
