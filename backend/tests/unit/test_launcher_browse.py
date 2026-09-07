"""_validate_browse_path and browse() in isolation — no FastAPI."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.api.v1.launcher import service
from app.api.v1.launcher.service import _validate_browse_path
from app.core.exceptions import InvalidBrowsePathError


def test_accepts_an_absolute_existing_directory(tmp_path: Path) -> None:
    assert _validate_browse_path(str(tmp_path)) == tmp_path.resolve()


def test_expands_tilde(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    assert _validate_browse_path("~") == tmp_path.resolve()


def test_rejects_a_relative_path() -> None:
    with pytest.raises(InvalidBrowsePathError):
        _validate_browse_path("relative/path")


def test_rejects_a_nonexistent_path(tmp_path: Path) -> None:
    with pytest.raises(InvalidBrowsePathError):
        _validate_browse_path(str(tmp_path / "nope"))


def test_rejects_a_file(tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("x", encoding="utf-8")
    with pytest.raises(InvalidBrowsePathError):
        _validate_browse_path(str(f))


async def test_browse_skips_an_entry_that_raises_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real chmod tricks can't reliably force a per-entry PermissionError (the
    OS only needs execute on the parent, not the entry, to stat it) — so the
    skip is exercised directly against a faulty Path.is_dir."""
    (tmp_path / "alpha").mkdir()
    (tmp_path / "beta").mkdir()
    (tmp_path / "locked").mkdir()
    real_is_dir = Path.is_dir

    def flaky_is_dir(self: Path) -> bool:
        if self.name == "locked":
            raise PermissionError("simulated")
        return real_is_dir(self)

    monkeypatch.setattr(Path, "is_dir", flaky_is_dir)

    listing = await service.browse(db=None, path=str(tmp_path))  # type: ignore[arg-type]
    assert {e.name for e in listing.entries} == {"alpha", "beta"}
