"""Claude Code provider — scans globally installed skills and subagents.

Roots:
- skills: ``<skills_root>/<name>/SKILL.md``
- agents: ``<agents_root>/<name>.md``
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from app.providers.base import ScannedAsset

_KIND_SKILL = "skill"
_KIND_AGENT = "agent"


def parse_frontmatter(content: str) -> dict[str, Any]:
    """Return the YAML frontmatter as a dict. Never raises: malformed or missing
    frontmatter yields an empty dict so a single bad file can't break the scan.
    """
    if not content.startswith("---"):
        return {}
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {}
    block = "\n".join(lines[1:end])
    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _meta_title(meta: dict[str, Any], fallback: str) -> str:
    for key in ("name", "title"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


def _meta_description(meta: dict[str, Any]) -> str:
    value = meta.get("description")
    return value.strip() if isinstance(value, str) else ""


def _meta_model(meta: dict[str, Any]) -> str | None:
    value = meta.get("model")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)


def build_asset(
    provider: str, kind: str, name: str, path: Path, *, read_only: bool = False
) -> ScannedAsset | None:
    """Read one asset file into a ScannedAsset; None if the file is unreadable."""
    try:
        content = _read(path)
        updated_at = _mtime(path)
    except OSError:
        return None
    meta = parse_frontmatter(content)
    return ScannedAsset(
        provider=provider,
        kind=kind,
        name=name,
        title=_meta_title(meta, name),
        description=_meta_description(meta),
        path=path,
        updated_at=updated_at,
        content=content,
        read_only=read_only,
        model=_meta_model(meta),
    )


class ClaudeProvider:
    """Provider for Claude Code skills and subagents."""

    name = "claude"

    def __init__(self, skills_root: Path, agents_root: Path) -> None:
        self._skills_root = skills_root
        self._agents_root = agents_root

    def roots(self) -> list[Path]:
        return [self._skills_root, self._agents_root]

    def scan(self) -> Iterable[ScannedAsset]:
        yield from self._scan_skills()
        yield from self._scan_agents()

    def _scan_skills(self) -> Iterable[ScannedAsset]:
        if not self._skills_root.is_dir():
            return
        for entry in sorted(self._skills_root.iterdir()):
            skill_file = entry / "SKILL.md"
            if not entry.is_dir() or not skill_file.is_file():
                continue
            asset = self._build(_KIND_SKILL, entry.name, skill_file)
            if asset is not None:
                yield asset

    def _scan_agents(self) -> Iterable[ScannedAsset]:
        if not self._agents_root.is_dir():
            return
        for entry in sorted(self._agents_root.iterdir()):
            if not entry.is_file() or entry.suffix != ".md":
                continue
            asset = self._build(_KIND_AGENT, entry.stem, entry)
            if asset is not None:
                yield asset

    def _build(self, kind: str, name: str, path: Path) -> ScannedAsset | None:
        return build_asset(self.name, kind, name, path)

    def asset_id_for_path(self, path: Path) -> str | None:
        """Map a resolved absolute path back to an asset id, if it is one."""
        try:
            resolved = path.resolve()
        except (OSError, RuntimeError):
            return None
        skills_root = _safe_resolve(self._skills_root)
        agents_root = _safe_resolve(self._agents_root)
        if skills_root is not None and resolved.name == "SKILL.md":
            parent = resolved.parent
            if parent.parent == skills_root:
                return f"{self.name}:{_KIND_SKILL}:{parent.name}"
        if agents_root is not None and resolved.suffix == ".md" and resolved.parent == agents_root:
            return f"{self.name}:{_KIND_AGENT}:{resolved.stem}"
        return None


def _safe_resolve(path: Path) -> Path | None:
    try:
        return path.resolve()
    except (OSError, RuntimeError):
        return None
