"""Path validation for PUT/apply: writes must resolve inside a provider root."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.api.v1.assets.service import get_asset, update_asset
from app.core.exceptions import AssetNotFoundError, InvalidAssetIdError
from app.providers.base import resolve_within_roots
from app.providers.claude import ClaudeProvider


def _provider(tree: tuple[Path, Path]) -> ClaudeProvider:
    skills_root, agents_root = tree
    return ClaudeProvider(skills_root=skills_root, agents_root=agents_root)


def test_resolve_within_roots_accepts_inside(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "sub" / "file.md"
    assert resolve_within_roots(target, [root]) == target.resolve()


def test_resolve_within_roots_rejects_traversal(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    escaped = root / ".." / "outside.md"
    assert resolve_within_roots(escaped, [root]) is None


def test_resolve_within_roots_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    link = root / "link.md"
    link.symlink_to(outside)
    assert resolve_within_roots(link, [root]) is None


async def test_update_asset_writes_valid_file(claude_tree: tuple[Path, Path]) -> None:
    providers = [_provider(claude_tree)]
    detail = await update_asset(
        providers,
        "claude:skill:frontend-dev",
        "---\nname: frontend-dev\ndescription: Updated.\n---\n\nNew body.\n",
    )
    assert detail.description == "Updated."
    assert "New body." in detail.content
    skills_root = claude_tree[0]
    on_disk = (skills_root / "frontend-dev" / "SKILL.md").read_text(encoding="utf-8")
    assert "New body." in on_disk


async def test_update_asset_refuses_symlink_escape(claude_tree: tuple[Path, Path]) -> None:
    skills_root, agents_root = claude_tree
    outside = agents_root.parent / "outside.md"
    outside.write_text("original", encoding="utf-8")
    evil = skills_root / "evil"
    evil.mkdir()
    (evil / "SKILL.md").symlink_to(outside)

    providers = [_provider(claude_tree)]
    with pytest.raises(InvalidAssetIdError):
        await update_asset(providers, "claude:skill:evil", "hacked")
    assert outside.read_text(encoding="utf-8") == "original"


def test_get_asset_unknown_raises(claude_tree: tuple[Path, Path]) -> None:
    with pytest.raises(AssetNotFoundError):
        get_asset([_provider(claude_tree)], "claude:skill:nope")


def test_malformed_asset_id_raises(claude_tree: tuple[Path, Path]) -> None:
    with pytest.raises(InvalidAssetIdError):
        get_asset([_provider(claude_tree)], "not-a-valid-id")
