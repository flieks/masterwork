"""Asset listing, filtering, and search (case-insensitive over all fields)."""

from __future__ import annotations

from pathlib import Path

from app.api.v1.assets.schemas import AssetKind
from app.api.v1.assets.service import list_assets
from app.providers.claude import ClaudeProvider


def _provider(tree: tuple[Path, Path]) -> ClaudeProvider:
    skills_root, agents_root = tree
    return ClaudeProvider(skills_root=skills_root, agents_root=agents_root)


def test_list_all(claude_tree: tuple[Path, Path]) -> None:
    assets = list_assets([_provider(claude_tree)])
    assert len(assets) == 3


def test_filter_by_kind(claude_tree: tuple[Path, Path]) -> None:
    skills = list_assets([_provider(claude_tree)], kind=AssetKind.skill)
    agents = list_assets([_provider(claude_tree)], kind=AssetKind.agent)
    assert {a.name for a in skills} == {"frontend-dev", "backend-dev"}
    assert {a.name for a in agents} == {"architect"}


def test_search_matches_title(claude_tree: tuple[Path, Path]) -> None:
    results = list_assets([_provider(claude_tree)], q="FRONTEND")
    assert {a.name for a in results} == {"frontend-dev"}


def test_search_matches_description(claude_tree: tuple[Path, Path]) -> None:
    results = list_assets([_provider(claude_tree)], q="fastapi")
    assert {a.name for a in results} == {"backend-dev"}


def test_search_matches_body_content(claude_tree: tuple[Path, Path]) -> None:
    # "Vite" only appears in the frontend skill body, not its metadata.
    results = list_assets([_provider(claude_tree)], q="vite")
    assert {a.name for a in results} == {"frontend-dev"}


def test_search_and_kind_combine(claude_tree: tuple[Path, Path]) -> None:
    results = list_assets([_provider(claude_tree)], kind=AssetKind.agent, q="design")
    assert {a.name for a in results} == {"architect"}
    # "design" appears in the architect body but the skill filter excludes skills.
    assert list_assets([_provider(claude_tree)], kind=AssetKind.skill, q="design") == []
