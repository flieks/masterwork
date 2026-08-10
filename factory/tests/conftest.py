"""Shared fixtures: a real temp git repo and a fake `claude` on PATH."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

FACTORY_DIR = Path(__file__).resolve().parents[1]
if str(FACTORY_DIR) not in sys.path:
    sys.path.insert(0, str(FACTORY_DIR))

FAKE_SOURCE = Path(__file__).resolve().parent / "fake_claude.py"


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, check=True)
    return proc.stdout


@pytest.fixture(autouse=True)
def isolated_roles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """No test may read — or seed into — the developer's real ~/.masterwork/agents."""
    library = tmp_path / "roles"
    monkeypatch.setenv("MASTERWORK_ROLES_DIR", str(library))
    return library


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """A real repo with one commit — no git is ever mocked in these tests."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "factory@test.local")
    git(repo, "config", "user.name", "Factory Test")
    git(repo, "config", "commit.gpgsign", "false")
    (repo / "README.md").write_text("# fixture repo\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "initial")
    return repo


class FakeCLI:
    """Owns the scripted behaviour of the fake `claude` binary for one test."""

    def __init__(self, bin_dir: Path, script_path: Path, log_path: Path, state_path: Path) -> None:
        self.bin_dir = bin_dir
        self.script_path = script_path
        self.log_path = log_path
        self.state_path = state_path

    def script(self, invocations: list[dict], default: dict | None = None) -> None:
        self.script_path.write_text(
            json.dumps({"invocations": invocations, "default": default}), encoding="utf-8"
        )

    @property
    def calls(self) -> list[dict]:
        if not self.log_path.is_file():
            return []
        return [json.loads(line) for line in self.log_path.read_text().splitlines() if line.strip()]


@pytest.fixture
def fake_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeCLI:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    target = bin_dir / "claude"
    shutil.copy(FAKE_SOURCE, target)
    target.chmod(0o755)

    script_path = tmp_path / "fake_script.json"
    log_path = tmp_path / "fake_calls.jsonl"
    state_path = tmp_path / "fake_state"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FACTORY_FAKE_SCRIPT", str(script_path))
    monkeypatch.setenv("FACTORY_FAKE_LOG", str(log_path))
    monkeypatch.setenv("FACTORY_FAKE_STATE", str(state_path))
    cli = FakeCLI(bin_dir, script_path, log_path, state_path)
    cli.script([])
    return cli


class PostSpy:
    """Stands in for the masterwork collector: every telemetry POST body, in order."""

    def __init__(self) -> None:
        self.bodies: list[dict] = []
        self.requests: list[dict] = []

    def of(self, event_type: str) -> list[dict]:
        return [body for body in self.bodies if body.get("event_type") == event_type]


@pytest.fixture
def post_spy(monkeypatch: pytest.MonkeyPatch) -> PostSpy:
    spy = PostSpy()

    class _Response:
        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *exc: object) -> bool:
            return False

    def fake_urlopen(request, timeout=None):  # noqa: ANN001, ANN202
        spy.requests.append({"url": request.full_url, "method": request.get_method()})
        spy.bodies.append(json.loads(request.data.decode()))
        return _Response()

    monkeypatch.setattr("adw.telemetry.urllib.request.urlopen", fake_urlopen)
    return spy


def envelope(**overrides: object) -> dict:
    """A well-formed envelope; override any field per test."""
    base: dict[str, object] = {
        "status": "ok",
        "summary": "Did the thing.",
        "artifacts": [],
        "notes_for_next_agent": "",
        "changed_files": [],
        "approved": False,
        "blocking": [],
        "assumptions": [],
    }
    base.update(overrides)
    return base
