"""Remote-URL normalization, git-config parsing (no subprocess), and the
three-step local-path resolution (stored mapping -> projects_root scan ->
unresolved)."""

from __future__ import annotations

from pathlib import Path

from app.repositories import work as work_repo
from app.services.repo_paths import normalize_remote_url, read_git_origin, resolve_local_path

# --- normalize_remote_url --------------------------------------------------


def test_ssh_and_https_forms_of_one_azure_repo_normalize_equal() -> None:
    ssh = normalize_remote_url("git@ssh.dev.azure.com:v3/acme/widgets/api")
    https = normalize_remote_url("https://dev.azure.com/acme/widgets/_git/api")
    assert ssh == https == "https://dev.azure.com/acme/widgets/_git/api"


def test_visualstudio_ssh_host_form_also_normalizes_equal() -> None:
    ssh = normalize_remote_url("git@vs-ssh.acme.visualstudio.com:v3/acme/widgets/api")
    https = normalize_remote_url("https://dev.azure.com/acme/widgets/_git/api")
    assert ssh == https


def test_trailing_dot_git_is_dropped() -> None:
    assert normalize_remote_url(
        "https://dev.azure.com/acme/widgets/_git/api.git"
    ) == normalize_remote_url("https://dev.azure.com/acme/widgets/_git/api")


def test_trailing_slash_is_dropped() -> None:
    assert normalize_remote_url(
        "https://dev.azure.com/acme/widgets/_git/api/"
    ) == normalize_remote_url("https://dev.azure.com/acme/widgets/_git/api")


def test_embedded_pat_credentials_are_dropped() -> None:
    with_pat = normalize_remote_url("https://user:sometoken@dev.azure.com/acme/widgets/_git/api")
    assert with_pat == normalize_remote_url("https://dev.azure.com/acme/widgets/_git/api")


def test_embedded_org_username_without_password_is_dropped() -> None:
    with_org = normalize_remote_url("https://acme@dev.azure.com/acme/widgets/_git/api")
    assert with_org == normalize_remote_url("https://dev.azure.com/acme/widgets/_git/api")


def test_host_case_is_folded() -> None:
    assert normalize_remote_url(
        "https://DEV.AZURE.COM/acme/widgets/_git/api"
    ) == normalize_remote_url("https://dev.azure.com/acme/widgets/_git/api")


def test_a_generic_github_scp_url_also_normalizes() -> None:
    assert normalize_remote_url("git@github.com:org/repo.git") == "https://github.com/org/repo"


def test_empty_input_returns_empty_string_without_raising() -> None:
    assert normalize_remote_url("") == ""
    assert normalize_remote_url("   ") == ""


def test_garbage_input_does_not_raise() -> None:
    normalize_remote_url("not a url at all, just words")
    normalize_remote_url("::::")


# --- read_git_origin --------------------------------------------------------


def test_read_git_origin_returns_the_configured_url(tmp_path: Path) -> None:
    repo = tmp_path / "checkout"
    (repo / ".git").mkdir(parents=True)
    (repo / ".git" / "config").write_text(
        '[remote "origin"]\n\turl = https://dev.azure.com/acme/widgets/_git/api\n'
        "\tfetch = +refs/heads/*:refs/remotes/origin/*\n",
        encoding="utf-8",
    )
    assert read_git_origin(repo) == "https://dev.azure.com/acme/widgets/_git/api"


def test_read_git_origin_returns_none_with_no_git_dir(tmp_path: Path) -> None:
    assert read_git_origin(tmp_path / "not-a-repo") is None


def test_read_git_origin_returns_none_with_no_origin_remote(tmp_path: Path) -> None:
    repo = tmp_path / "checkout"
    (repo / ".git").mkdir(parents=True)
    (repo / ".git" / "config").write_text("[core]\n\tbare = false\n", encoding="utf-8")
    assert read_git_origin(repo) is None


def test_read_git_origin_returns_none_on_a_malformed_config(tmp_path: Path) -> None:
    repo = tmp_path / "checkout"
    (repo / ".git").mkdir(parents=True)
    (repo / ".git" / "config").write_text("this is not [a valid ini file", encoding="utf-8")
    assert read_git_origin(repo) is None


def test_read_git_origin_treats_a_git_file_as_no_origin(tmp_path: Path) -> None:
    """A worktree/submodule pointer — followed by real git, not by this parser."""
    repo = tmp_path / "worktree"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: ../main/.git/worktrees/worktree\n", encoding="utf-8")
    assert read_git_origin(repo) is None


# --- resolve_local_path ------------------------------------------------------


def _write_origin(repo: Path, url: str) -> None:
    (repo / ".git").mkdir(parents=True)
    (repo / ".git" / "config").write_text(f'[remote "origin"]\n\turl = {url}\n', encoding="utf-8")


async def test_resolve_local_path_stored_mapping_wins_and_no_scan_happens(
    session_factory, tmp_path: Path
) -> None:
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    # A folder that WOULD match by scan, proving the stored hit short-circuits it.
    decoy = projects_root / "decoy"
    _write_origin(decoy, "https://dev.azure.com/acme/widgets/_git/api")

    stored_path = tmp_path / "elsewhere" / "my-checkout"
    stored_path.mkdir(parents=True)
    remote = "https://dev.azure.com/acme/widgets/_git/api"

    async with session_factory() as db:
        await work_repo.upsert_repo_path(
            db, remote_url=normalize_remote_url(remote), local_path=str(stored_path)
        )
        await db.commit()
        resolution = await resolve_local_path(db, remote, projects_root)

    assert resolution.matched_from == "stored"
    assert resolution.local_path == stored_path


async def test_resolve_local_path_scans_projects_root_and_persists_the_match(
    session_factory, tmp_path: Path
) -> None:
    projects_root = tmp_path / "projects"
    # Folder name deliberately differs from the repo name.
    checkout = projects_root / "my-local-name"
    remote = "https://dev.azure.com/acme/widgets/_git/api"
    _write_origin(checkout, "git@ssh.dev.azure.com:v3/acme/widgets/api")  # other URL form

    async with session_factory() as db:
        resolution = await resolve_local_path(db, remote, projects_root)
        await db.commit()

        assert resolution.matched_from == "scan"
        assert resolution.local_path == checkout

        stored = await work_repo.get_repo_path(db, normalize_remote_url(remote))
        assert stored is not None
        assert stored.local_path == str(checkout)


async def test_resolve_local_path_returns_unresolved_with_a_reason(
    session_factory, tmp_path: Path
) -> None:
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    (projects_root / "unrelated").mkdir()  # a folder, but not a git repo

    async with session_factory() as db:
        resolution = await resolve_local_path(
            db, "https://dev.azure.com/acme/widgets/_git/api", projects_root
        )

    assert resolution.matched_from == "none"
    assert resolution.local_path is None
    assert resolution.reason
    assert "acme/widgets" in resolution.reason


async def test_resolve_local_path_falls_through_to_scan_when_stored_path_vanished(
    session_factory, tmp_path: Path
) -> None:
    projects_root = tmp_path / "projects"
    remote = "https://dev.azure.com/acme/widgets/_git/api"
    checkout = projects_root / "widgets-checkout"
    _write_origin(checkout, remote)

    async with session_factory() as db:
        await work_repo.upsert_repo_path(
            db,
            remote_url=normalize_remote_url(remote),
            local_path=str(tmp_path / "gone" / "moved-away"),
        )
        await db.commit()
        resolution = await resolve_local_path(db, remote, projects_root)

    assert resolution.matched_from == "scan"
    assert resolution.local_path == checkout
