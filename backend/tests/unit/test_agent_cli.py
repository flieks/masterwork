"""Binary resolution and the effective-agent rule, on tmp dirs only."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from app.config import Settings
from app.services.agent_cli import (
    AgentId,
    detect_agent_bins,  # bound before conftest patches the module attribute
    effective_agent,
    fallback_bins,
    resolve_bin,
)


def _exe(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_resolve_prefers_the_path(tmp_path: Path) -> None:
    on_path = _exe(tmp_path / "bin" / "codex")
    fallback = _exe(tmp_path / "app" / "codex")
    assert resolve_bin("codex", [fallback], path=str(on_path.parent)) == str(on_path)


def test_resolve_falls_back_to_an_executable_install_location(tmp_path: Path) -> None:
    not_executable = tmp_path / "first" / "codex"
    not_executable.parent.mkdir()
    not_executable.write_text("", encoding="utf-8")
    fallback = _exe(tmp_path / "second" / "codex")
    empty_path = tmp_path / "empty"
    empty_path.mkdir()

    assert resolve_bin("codex", [not_executable, fallback], path=str(empty_path)) == str(fallback)


def test_resolve_returns_none_when_nothing_matches(tmp_path: Path) -> None:
    assert resolve_bin("codex", [tmp_path / "missing"], path=str(tmp_path)) is None


def test_codex_fallbacks_cover_the_app_bundles_and_local_bin(tmp_path: Path) -> None:
    candidates = fallback_bins(AgentId.CODEX, tmp_path)
    assert Path("/Applications/ChatGPT.app/Contents/Resources/codex") in candidates
    assert Path("/Applications/Codex.app/Contents/Resources/codex") in candidates
    assert tmp_path / ".local" / "bin" / "codex" in candidates
    assert fallback_bins(AgentId.CLAUDE, tmp_path) == (tmp_path / ".claude" / "local" / "claude",)


def test_detect_searches_the_process_path_and_the_home_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    path_dir = tmp_path / "path"
    claude = _exe(path_dir / "claude")
    codex = _exe(home / ".local" / "bin" / "codex")  # not on PATH, a per-user dir
    monkeypatch.setenv("PATH", str(path_dir))

    bins = detect_agent_bins(Settings(claude_bin="claude", codex_bin="codex"), home=home)

    assert bins[AgentId.CLAUDE] == str(claude)
    # Found on the extended PATH, before any app-bundle fallback is consulted.
    assert bins[AgentId.CODEX] == str(codex)


def test_effective_agent_prefers_the_stored_choice() -> None:
    bins = {AgentId.CLAUDE: "/bin/claude", AgentId.CODEX: None}
    assert effective_agent("codex", bins) is AgentId.CODEX


def test_effective_agent_unset_picks_the_first_installed() -> None:
    assert effective_agent(None, {AgentId.CLAUDE: None, AgentId.CODEX: "/c"}) is AgentId.CODEX
    assert effective_agent(None, {AgentId.CLAUDE: "/a", AgentId.CODEX: "/c"}) is AgentId.CLAUDE


def test_effective_agent_defaults_to_claude_and_ignores_junk() -> None:
    none = {AgentId.CLAUDE: None, AgentId.CODEX: None}
    assert effective_agent(None, none) is AgentId.CLAUDE
    assert effective_agent("cursor", {AgentId.CLAUDE: None, AgentId.CODEX: "/c"}) is AgentId.CODEX
