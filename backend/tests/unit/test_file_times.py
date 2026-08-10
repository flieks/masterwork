"""When an asset was created, across every provider and both kinds of platform.

`updated_at` alone cannot tell a skill written in July from one written
yesterday, which is the whole question the field answers.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.providers.base import Provider, file_times
from app.providers.claude import ClaudeProvider
from app.providers.claude_plugins import ClaudePluginProvider
from app.providers.masterwork_roles import MasterworkRoleProvider

# macOS and the BSDs record a birth time; Linux does not, and the assertions
# below have to hold on both — CI is Linux, this Mac is not.
HAS_BIRTHTIME = hasattr(Path(__file__).stat(), "st_birthtime")


class _FakeStat:
    """A stat result with a birth time only where one is asked for."""

    def __init__(self, mtime: float, birthtime: float | None) -> None:
        self.st_mtime = mtime
        if birthtime is not None:
            self.st_birthtime = birthtime


def _fake_stat(mtime: float, birthtime: float | None) -> object:
    return _FakeStat(mtime, birthtime)


def test_every_provider_dates_its_assets(
    claude_tree: tuple[Path, Path], plugin_tree: Path, role_tree: Path
) -> None:
    """Including the read-only plugin provider and the factory's role store — a
    provider that skipped it would show a blank column for no stated reason."""
    skills_root, agents_root = claude_tree
    providers: list[Provider] = [
        ClaudeProvider(skills_root=skills_root, agents_root=agents_root),
        ClaudePluginProvider(plugins_root=plugin_tree),
        MasterworkRoleProvider(store_root=role_tree),
    ]
    for provider in providers:
        assets = list(provider.scan())
        assert assets, f"{provider.name} scanned nothing"
        for asset in assets:
            assert (asset.created_at is not None) is HAS_BIRTHTIME, asset.id
            if asset.created_at is not None:
                assert asset.created_at <= asset.updated_at, asset.id


def test_a_platform_without_a_birth_time_reports_no_date_rather_than_a_wrong_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The mtime is the tempting fallback and it is a lie: it would date a skill
    edited yesterday to yesterday, which is exactly the confusion the field
    exists to end."""
    path = tmp_path / "SKILL.md"
    path.write_text("body", encoding="utf-8")
    monkeypatch.setattr(Path, "stat", lambda self, **kw: _fake_stat(1_700_000_000.0, None))

    updated, created = file_times(path)

    assert created is None
    assert updated == datetime.fromtimestamp(1_700_000_000.0, tz=UTC)


def test_a_file_that_was_copied_is_dated_by_its_content_not_its_inode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`cp -p` gives a July file a birth time of today. Reporting it would show
    a card created after it was last modified — false on its face."""
    path = tmp_path / "SKILL.md"
    path.write_text("body", encoding="utf-8")
    july, today = 1_720_000_000.0, 1_754_000_000.0
    monkeypatch.setattr(Path, "stat", lambda self, **kw: _fake_stat(july, today))

    updated, created = file_times(path)

    assert created == updated == datetime.fromtimestamp(july, tz=UTC)


def test_a_birth_time_older_than_the_mtime_is_kept(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The ordinary case: written in July, edited since."""
    path = tmp_path / "SKILL.md"
    path.write_text("body", encoding="utf-8")
    july, august = 1_720_000_000.0, 1_754_000_000.0
    monkeypatch.setattr(Path, "stat", lambda self, **kw: _fake_stat(august, july))

    updated, created = file_times(path)

    assert created == datetime.fromtimestamp(july, tz=UTC)
    assert updated == datetime.fromtimestamp(august, tz=UTC)
