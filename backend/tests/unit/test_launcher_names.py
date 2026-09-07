"""_validate_project_name in isolation — no DB, no filesystem."""

from __future__ import annotations

import pytest

from app.api.v1.launcher.service import _validate_project_name
from app.core.exceptions import InvalidProjectNameError


@pytest.mark.parametrize("name", ["my-app", "Deploy_pipeline", "a" * 100])
def test_accepts_ordinary_names(name: str) -> None:
    assert _validate_project_name(name) == name


@pytest.mark.parametrize(
    "name",
    [
        "",
        "   ",
        "../evil",
        "a/b",
        "a\\b",
        ".",
        "..",
        ".hidden",
        "a" * 101,
        "bad\x00name",
    ],
)
def test_rejects_invalid_names(name: str) -> None:
    with pytest.raises(InvalidProjectNameError):
        _validate_project_name(name)
