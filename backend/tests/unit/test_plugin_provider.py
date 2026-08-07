"""ClaudePluginProvider: manifest-driven read-only scan of plugin assets."""

from __future__ import annotations

import json
from pathlib import Path

from app.providers.claude_plugins import ClaudePluginProvider


def test_scan_plugin_skills_and_agents(plugin_tree: Path) -> None:
    assets = list(ClaudePluginProvider(plugins_root=plugin_tree).scan())
    by_id = {a.id: a for a in assets}
    assert set(by_id) == {
        "claude-plugin:skill:vercel:bootstrap",
        "claude-plugin:agent:vercel:deployment-expert",
    }
    skill = by_id["claude-plugin:skill:vercel:bootstrap"]
    assert skill.read_only is True
    assert skill.provider == "claude-plugin"
    assert skill.name == "vercel:bootstrap"
    assert skill.description == "Provision Vercel resources."
    # .md.tmpl template files never become assets
    assert not any("tmpl" in a.id for a in assets)


def test_no_writable_roots(plugin_tree: Path) -> None:
    assert ClaudePluginProvider(plugins_root=plugin_tree).roots() == []


def test_missing_or_malformed_manifest(tmp_path: Path) -> None:
    assert list(ClaudePluginProvider(plugins_root=tmp_path).scan()) == []
    (tmp_path / "installed_plugins.json").write_text("{not json", encoding="utf-8")
    assert list(ClaudePluginProvider(plugins_root=tmp_path).scan()) == []
    (tmp_path / "installed_plugins.json").write_text(json.dumps({"plugins": 3}), encoding="utf-8")
    assert list(ClaudePluginProvider(plugins_root=tmp_path).scan()) == []


def test_duplicate_scopes_dedupe(plugin_tree: Path) -> None:
    manifest = plugin_tree / "installed_plugins.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    entry = data["plugins"]["vercel@official"][0]
    data["plugins"]["vercel@official"].append({**entry, "scope": "project"})
    manifest.write_text(json.dumps(data), encoding="utf-8")
    assets = list(ClaudePluginProvider(plugins_root=plugin_tree).scan())
    assert len(assets) == 2  # not doubled


def test_asset_id_for_path(plugin_tree: Path) -> None:
    provider = ClaudePluginProvider(plugins_root=plugin_tree)
    install = plugin_tree / "cache" / "official" / "vercel" / "1.0.0"
    skill_path = install / "skills" / "bootstrap" / "SKILL.md"
    agent_path = install / "agents" / "deployment-expert.md"
    assert provider.asset_id_for_path(skill_path) == "claude-plugin:skill:vercel:bootstrap"
    assert provider.asset_id_for_path(agent_path) == "claude-plugin:agent:vercel:deployment-expert"
    assert provider.asset_id_for_path(plugin_tree / "installed_plugins.json") is None
