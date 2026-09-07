"""PR/thread field mapping and delegate-prompt assembly.

Sync functions upsert through app.repositories.work, which needs a real,
FK-enforced session (a source/PR row must actually exist for the insert to
succeed) — so these use the real test-DB session_factory fixture with a fake
DevOps client, never a live network call. `assemble_pr_prompt` is pure and is
tested against plain (unpersisted) ORM instances, exactly like
test_work_sync.py's `_source()`.
"""

from __future__ import annotations

from app.db.models.work import WorkPrThread, WorkPullRequest, WorkSource
from app.repositories import work as work_repo
from app.services import work_prs
from app.services.work_sync import SyncCounts


class _FakeDevOpsClient:
    """Records nothing; hands back whatever payloads the test configured."""

    def __init__(self, prs: list[dict] | None = None, threads: list[dict] | None = None) -> None:
        self._prs = prs if prs is not None else []
        self._threads = threads if threads is not None else []

    async def list_active_prs(self) -> list[dict]:
        return self._prs

    async def list_pr_threads(self, repository_id: str, pr_id: int) -> list[dict]:
        return self._threads


def _pr_payload(**overrides: object) -> dict:
    payload: dict = {
        "pullRequestId": 501,
        "repository": {
            "id": "repo-guid-1",
            "name": "widgets-api",
            "remoteUrl": "https://dev.azure.com/acme/widgets/_git/widgets-api",
        },
        "title": "Fix the retry button",
        "description": "Handles the flaky retry case.",
        "sourceRefName": "refs/heads/feature/retry-fix",
        "targetRefName": "refs/heads/main",
        "status": "active",
        "isDraft": False,
        "createdBy": {"displayName": "Alex Doe"},
        "creationDate": "2026-08-10T09:00:00Z",
    }
    payload.update(overrides)
    return payload


def _thread_payload(**overrides: object) -> dict:
    payload: dict = {
        "id": 1,
        "status": "active",
        "comments": [
            {
                "id": 1,
                "author": {"displayName": "Sam Reviewer"},
                "content": "This branch can throw on empty input.",
                "commentType": "text",
                "publishedDate": "2026-08-11T10:00:00Z",
            }
        ],
    }
    payload.update(overrides)
    return payload


async def _new_source(session_factory) -> WorkSource:
    async with session_factory() as db:
        source = WorkSource(org_url="https://dev.azure.com/acme", project="widgets")
        db.add(source)
        await db.commit()
        await db.refresh(source)
        return source


async def _synced_pr(session_factory, source: WorkSource, **payload_overrides: object):
    client = _FakeDevOpsClient(prs=[_pr_payload(**payload_overrides)])
    async with session_factory() as db:
        await work_prs.sync_pull_requests(db, source, client)
        await db.commit()
        prs = await work_repo.list_prs(db, source_id=source.id)
        return prs[0]


# --- sync_pull_requests --------------------------------------------------


async def test_sync_pull_requests_maps_fields_and_strips_ref_prefixes(session_factory) -> None:
    source = await _new_source(session_factory)
    client = _FakeDevOpsClient(prs=[_pr_payload()])

    async with session_factory() as db:
        counts = await work_prs.sync_pull_requests(db, source, client)
        await db.commit()

    assert counts == SyncCounts(fetched=1, inserted=1, updated=0)

    async with session_factory() as db:
        prs = await work_repo.list_prs(db, source_id=source.id)
    assert len(prs) == 1
    pr = prs[0]
    assert pr.external_id == 501
    assert pr.repository_id == "repo-guid-1"
    assert pr.repository_name == "widgets-api"
    assert pr.repository_remote_url == "https://dev.azure.com/acme/widgets/_git/widgets-api"
    assert pr.source_branch == "feature/retry-fix"
    assert pr.target_branch == "main"
    assert pr.is_draft is False
    assert pr.created_by == "Alex Doe"
    assert pr.external_url == (
        "https://dev.azure.com/acme/widgets/_git/widgets-api/pullrequest/501"
    )


async def test_sync_pull_requests_falls_back_to_web_url_when_remote_url_absent(
    session_factory,
) -> None:
    source = await _new_source(session_factory)
    payload = _pr_payload(
        repository={
            "id": "repo-guid-2",
            "name": "widgets-web",
            "webUrl": "https://dev.azure.com/acme/_git/widgets-web",
        }
    )
    client = _FakeDevOpsClient(prs=[payload])

    async with session_factory() as db:
        await work_prs.sync_pull_requests(db, source, client)
        await db.commit()
        prs = await work_repo.list_prs(db, source_id=source.id)

    assert prs[0].repository_remote_url == "https://dev.azure.com/acme/_git/widgets-web"


async def test_sync_pull_requests_skips_a_payload_without_an_id(session_factory) -> None:
    source = await _new_source(session_factory)
    payload = _pr_payload()
    del payload["pullRequestId"]
    client = _FakeDevOpsClient(prs=[payload])

    async with session_factory() as db:
        counts = await work_prs.sync_pull_requests(db, source, client)
        await db.commit()

    assert counts == SyncCounts(fetched=1, inserted=0, updated=0)


async def test_second_sync_updates_without_duplicating(session_factory) -> None:
    source = await _new_source(session_factory)
    await _synced_pr(session_factory, source)

    client = _FakeDevOpsClient(prs=[_pr_payload(title="Fix the retry button, take 2")])
    async with session_factory() as db:
        counts = await work_prs.sync_pull_requests(db, source, client)
        await db.commit()
        prs = await work_repo.list_prs(db, source_id=source.id)

    assert counts == SyncCounts(fetched=1, inserted=0, updated=1)
    assert len(prs) == 1
    assert prs[0].title == "Fix the retry button, take 2"


# --- sync_pr_threads -------------------------------------------------------


async def test_sync_pr_threads_derives_is_resolved_from_status(session_factory) -> None:
    source = await _new_source(session_factory)
    pr = await _synced_pr(session_factory, source)

    payloads = [
        _thread_payload(id=1, status="active"),
        _thread_payload(id=2, status="fixed"),
        _thread_payload(id=3, status="closed"),
        _thread_payload(id=4, status="wontFix"),
        _thread_payload(id=5, status="byDesign"),
    ]
    client = _FakeDevOpsClient(threads=payloads)

    async with session_factory() as db:
        threads = await work_prs.sync_pr_threads(db, pr, client)
        await db.commit()

    resolved_by_id = {t.external_id: t.is_resolved for t in threads}
    assert resolved_by_id == {1: False, 2: True, 3: True, 4: True, 5: True}


async def test_sync_pr_threads_reads_file_path_and_line_from_thread_context(
    session_factory,
) -> None:
    source = await _new_source(session_factory)
    pr = await _synced_pr(session_factory, source)

    with_line = _thread_payload(
        id=1,
        threadContext={"filePath": "/app/main.py", "rightFileStart": {"line": 42}},
    )
    fallback_line = _thread_payload(
        id=2,
        threadContext={"filePath": "/app/utils.py", "rightFileEnd": {"line": 7}},
    )
    pr_level = _thread_payload(id=3)  # no threadContext at all
    client = _FakeDevOpsClient(threads=[with_line, fallback_line, pr_level])

    async with session_factory() as db:
        threads = await work_prs.sync_pr_threads(db, pr, client)
        await db.commit()

    by_id = {t.external_id: t for t in threads}
    assert by_id[1].file_path == "/app/main.py"
    assert by_id[1].right_file_line == 42
    assert by_id[2].file_path == "/app/utils.py"
    assert by_id[2].right_file_line == 7
    assert by_id[3].file_path is None
    assert by_id[3].right_file_line is None


async def test_sync_pr_threads_is_idempotent_and_reflects_a_changed_comment(
    session_factory,
) -> None:
    source = await _new_source(session_factory)
    pr = await _synced_pr(session_factory, source)
    client = _FakeDevOpsClient(threads=[_thread_payload(id=1)])

    async with session_factory() as db:
        await work_prs.sync_pr_threads(db, pr, client)
        await db.commit()

    updated_client = _FakeDevOpsClient(
        threads=[
            _thread_payload(
                id=1,
                comments=[
                    {
                        "id": 1,
                        "author": {"displayName": "Sam Reviewer"},
                        "content": "Actually, never mind — this is fine.",
                        "commentType": "text",
                        "publishedDate": "2026-08-11T11:00:00Z",
                    }
                ],
            )
        ]
    )
    async with session_factory() as db:
        threads = await work_prs.sync_pr_threads(db, pr, updated_client)
        await db.commit()

    assert len(threads) == 1  # no duplicate row
    assert threads[0].comments[0]["content"] == "Actually, never mind — this is fine."


# --- assemble_pr_prompt ----------------------------------------------------


def _pr(**overrides: object) -> WorkPullRequest:
    defaults = {
        "id": 1,
        "external_id": 501,
        "title": "Fix the retry button",
        "source_branch": "feature/retry-fix",
        "target_branch": "main",
        "repository_name": "widgets-api",
        "external_url": "https://dev.azure.com/acme/widgets/_git/widgets-api/pullrequest/501",
    }
    return WorkPullRequest(**{**defaults, **overrides})  # type: ignore[arg-type]


def _thread(**overrides: object) -> WorkPrThread:
    defaults = {
        "id": 1,
        "external_id": 1,
        "is_resolved": False,
        "file_path": None,
        "right_file_line": None,
        "comments": [],
    }
    return WorkPrThread(**{**defaults, **overrides})  # type: ignore[arg-type]


def _comment(author: str = "Sam Reviewer", content: str = "Please fix this.") -> dict:
    return {
        "id": 1,
        "author": author,
        "content": content,
        "comment_type": "text",
        "published_at": "2026-08-11T10:00:00Z",
    }


def test_assemble_pr_prompt_includes_identity_and_checkout_instruction() -> None:
    prompt = work_prs.assemble_pr_prompt(_pr(), [])
    assert "Pull request #501: Fix the retry button" in prompt
    assert "https://dev.azure.com/acme/widgets/_git/widgets-api/pullrequest/501" in prompt
    assert "Repository: widgets-api" in prompt
    assert "Branch: feature/retry-fix -> main" in prompt
    assert "Check out `feature/retry-fix` before doing anything else." in prompt


def test_assemble_pr_prompt_includes_an_unresolved_files_line_and_comments_in_order() -> None:
    thread = _thread(
        file_path="app/main.py",
        right_file_line=42,
        comments=[_comment("Sam Reviewer", "First."), _comment("Alex Doe", "Second.")],
    )
    prompt = work_prs.assemble_pr_prompt(_pr(), [thread])
    assert "### app/main.py:42" in prompt
    first = prompt.index("Sam Reviewer: First.")
    second = prompt.index("Alex Doe: Second.")
    assert first < second


def test_assemble_pr_prompt_uses_a_pr_level_heading_when_file_path_is_null() -> None:
    thread = _thread(file_path=None, comments=[_comment()])
    prompt = work_prs.assemble_pr_prompt(_pr(), [thread])
    assert "### On the pull request" in prompt


def test_assemble_pr_prompt_omits_resolved_threads_entirely() -> None:
    resolved = _thread(
        id=2, external_id=2, is_resolved=True, comments=[_comment("Sam Reviewer", "Already fixed.")]
    )
    prompt = work_prs.assemble_pr_prompt(_pr(), [resolved])
    assert "Already fixed." not in prompt


def test_assemble_pr_prompt_omits_a_thread_with_no_comments() -> None:
    empty = _thread(comments=[])
    prompt = work_prs.assemble_pr_prompt(_pr(), [empty])
    assert "###" not in prompt


def test_assemble_pr_prompt_always_states_the_no_push_no_devops_rules() -> None:
    prompt = work_prs.assemble_pr_prompt(_pr(), [])
    assert "Do NOT push" in prompt
    assert "Do NOT touch Azure DevOps" in prompt


def test_assemble_pr_prompt_handles_zero_unresolved_threads() -> None:
    resolved = _thread(is_resolved=True, comments=[_comment()])
    prompt = work_prs.assemble_pr_prompt(_pr(), [resolved])
    assert "(No unresolved review comments.)" in prompt
