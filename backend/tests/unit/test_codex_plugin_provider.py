"""CodexPluginProvider (v1.49): read-only plugin skills from Codex's cache, one
version per plugin, switched on only by config.toml."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.providers.codex_plugins import CodexPluginProvider


def _version(plugin_dir: Path, version: str, skills: dict[str, str], *, mtime: float) -> Path:
    root = plugin_dir / version
    (root / ".codex-plugin").mkdir(parents=True)
    (root / ".codex-plugin" / "plugin.json").write_text(
        json.dumps({"name": plugin_dir.name, "skills": "./skills/"}), encoding="utf-8"
    )
    for name, body in skills.items():
        (root / "skills" / name).mkdir(parents=True)
        (root / "skills" / name / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {body}\n---\n", encoding="utf-8"
        )
    os.utime(root, (mtime, mtime))
    return root


@pytest.fixture
def plugins(tmp_path: Path) -> Path:
    root = tmp_path / "plugins"
    chrome = root / "cache" / "openai-bundled" / "chrome"
    _version(chrome, "26.801.1", {"browse": "old copy"}, mtime=1_000)
    newest = _version(chrome, "26.903.71938", {"browse": "new copy"}, mtime=2_000)
    (chrome / "latest").symlink_to(newest, target_is_directory=True)
    docs = root / "cache" / "openai-primary-runtime" / "documents"
    _version(docs, "1.0.0", {"docx": "Word files"}, mtime=1_500)
    (root / ".plugin-appserver").mkdir()
    (root / "cache" / "openai-bundled" / "chrome" / "no-manifest").mkdir()
    return root


def _config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_the_newest_version_wins_and_a_latest_link_is_not_a_second_one(
    plugins: Path, tmp_path: Path
) -> None:
    config = _config(tmp_path, '[plugins."chrome@openai-bundled"]\nenabled = true\n')
    provider = CodexPluginProvider(plugins_root=plugins, config_file=config)
    by_id = {a.id: a for a in provider.scan()}
    assert set(by_id) == {"codex-plugin:skill:chrome:browse", "codex-plugin:skill:documents:docx"}
    browse = by_id["codex-plugin:skill:chrome:browse"]
    assert browse.description == "new copy"
    # The real version folder, so the path says which version was picked.
    assert "26.903.71938" in str(browse.path) and "latest" not in str(browse.path)
    assert browse.read_only is True
    assert provider.roots() == []


def test_enabled_comes_from_config_toml(plugins: Path, tmp_path: Path) -> None:
    config = _config(
        tmp_path,
        '[plugins."chrome@openai-bundled"]\nenabled = true\n\n'
        '[plugins."documents@openai-primary-runtime"]\nenabled = false\n',
    )
    by_name = {a.name: a for a in CodexPluginProvider(plugins, config_file=config).scan()}
    assert by_name["chrome:browse"].disabled is False
    assert by_name["chrome:browse"].agents == ("codex",)
    assert by_name["documents:docx"].disabled is True
    assert by_name["documents:docx"].disabled_by == "codex-config"
    assert by_name["documents:docx"].agents == ()


def test_a_plugin_config_never_mentions_is_not_enabled(plugins: Path, tmp_path: Path) -> None:
    provider = CodexPluginProvider(plugins, config_file=tmp_path / "missing.toml")
    assert all(a.disabled and a.disabled_by == "codex-config" for a in provider.scan())


def test_a_manifest_pointing_outside_its_version_is_not_followed(tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    evil = _version(root / "cache" / "m" / "evil", "1.0.0", {"inside": "ok"}, mtime=1)
    (evil / ".codex-plugin" / "plugin.json").write_text(
        json.dumps({"skills": "../../../../../outside"}), encoding="utf-8"
    )
    (tmp_path / "outside" / "stolen").mkdir(parents=True)
    (tmp_path / "outside" / "stolen" / "SKILL.md").write_text("x", encoding="utf-8")
    names = [a.name for a in CodexPluginProvider(root).scan()]
    assert names == ["evil:inside"]


def test_ids_map_back_from_paths_and_refs_match(plugins: Path) -> None:
    provider = CodexPluginProvider(plugins)
    skill = next(iter(provider.scan()))
    assert provider.asset_id_for_path(skill.path) == skill.id
    assert provider.asset_id_for_path(plugins / "cache") is None
    assert {r.id for r in provider.asset_refs()} == {a.id for a in provider.scan()}


def test_no_cache_is_no_assets(tmp_path: Path) -> None:
    assert list(CodexPluginProvider(tmp_path / "nothing").scan()) == []
