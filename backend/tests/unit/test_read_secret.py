"""read_secret: env var first, backend/.env fallback for the launchd-run backend."""

from pathlib import Path

import pytest

from app import config
from app.config import read_secret


@pytest.fixture
def dotenv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the fallback at a temp .env instead of the real backend/.env."""
    path = tmp_path / ".env"
    monkeypatch.setattr(config, "_ENV_PATH", path)
    return path


def test_env_var_wins(monkeypatch: pytest.MonkeyPatch, dotenv: Path) -> None:
    dotenv.write_text("MW_TEST_SECRET=from-dotenv\n")
    monkeypatch.setenv("MW_TEST_SECRET", "from-env")
    assert read_secret("MW_TEST_SECRET") == "from-env"


def test_falls_back_to_dotenv(monkeypatch: pytest.MonkeyPatch, dotenv: Path) -> None:
    monkeypatch.delenv("MW_TEST_SECRET", raising=False)
    dotenv.write_text("MW_TEST_SECRET=from-dotenv\n")
    assert read_secret("MW_TEST_SECRET") == "from-dotenv"


def test_missing_everywhere_is_none(monkeypatch: pytest.MonkeyPatch, dotenv: Path) -> None:
    monkeypatch.delenv("MW_TEST_SECRET", raising=False)
    assert read_secret("MW_TEST_SECRET") is None


def test_empty_env_var_falls_through(monkeypatch: pytest.MonkeyPatch, dotenv: Path) -> None:
    monkeypatch.setenv("MW_TEST_SECRET", "")
    dotenv.write_text("MW_TEST_SECRET=from-dotenv\n")
    assert read_secret("MW_TEST_SECRET") == "from-dotenv"


def test_fallback_is_not_cwd_dependent(
    monkeypatch: pytest.MonkeyPatch, dotenv: Path, tmp_path: Path
) -> None:
    """launchd runs the backend from the repo root, not backend/."""
    monkeypatch.delenv("MW_TEST_SECRET", raising=False)
    dotenv.write_text("MW_TEST_SECRET=from-dotenv\n")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert read_secret("MW_TEST_SECRET") == "from-dotenv"
