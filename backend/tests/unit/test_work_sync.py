"""HTML->markdown conversion, tag parsing, and WIQL selection — no network, no DB."""

from __future__ import annotations

from app.db.models.work import WorkSource
from app.providers.azuredevops import AzureDevOpsError
from app.services import work_sync


class _FakeDevOpsClient:
    """Records the WIQL it was asked, returns no ids — sync_source then makes
    no repository call at all, so this needs no real AsyncSession."""

    def __init__(
        self,
        current_iteration: str | None = None,
        iteration_fails: bool = False,
        owner_name: str | None = None,
        owner_fails: bool = False,
    ) -> None:
        self.wiql: str | None = None
        self._current_iteration = current_iteration
        self._iteration_fails = iteration_fails
        self._owner_name = owner_name
        self._owner_fails = owner_fails

    async def query_work_item_ids(self, wiql: str) -> list[int]:
        self.wiql = wiql
        return []

    async def get_work_items_batch(self, ids: list[int], fields: list[str]) -> list[dict]:
        return []

    async def get_current_iteration_path(self) -> str | None:
        if self._iteration_fails:
            raise AzureDevOpsError("boom")
        return self._current_iteration

    async def get_authenticated_user_display_name(self) -> str | None:
        if self._owner_fails:
            raise AzureDevOpsError("boom")
        return self._owner_name


class _FakeDB:
    async def flush(self) -> None:
        return None


def _source(**overrides: object) -> WorkSource:
    defaults = {"org_url": "https://dev.azure.com/acme", "project": "widgets"}
    return WorkSource(**{**defaults, **overrides})  # type: ignore[arg-type]


async def test_sync_uses_default_wiql_when_source_has_none() -> None:
    client = _FakeDevOpsClient()
    await work_sync.sync_source(_FakeDB(), _source(), client)  # type: ignore[arg-type]
    assert client.wiql == work_sync.DEFAULT_WIQL


async def test_sync_uses_the_sources_own_wiql_verbatim_when_set() -> None:
    custom = "SELECT [System.Id] FROM WorkItems WHERE [System.State] = 'Active'"
    client = _FakeDevOpsClient()
    source = _source(query_wiql=custom)
    await work_sync.sync_source(_FakeDB(), source, client)  # type: ignore[arg-type]
    assert client.wiql == custom


async def test_sync_stores_the_current_iteration_on_the_source() -> None:
    source = _source()
    client = _FakeDevOpsClient(current_iteration="widgets\\2026 Q3.2")
    await work_sync.sync_source(_FakeDB(), source, client)  # type: ignore[arg-type]
    assert source.current_iteration == "widgets\\2026 Q3.2"


async def test_sync_keeps_the_last_known_iteration_when_the_lookup_fails() -> None:
    source = _source(current_iteration="widgets\\2026 Q3.1")
    client = _FakeDevOpsClient(iteration_fails=True)
    await work_sync.sync_source(_FakeDB(), source, client)  # type: ignore[arg-type]
    assert source.current_iteration == "widgets\\2026 Q3.1"


async def test_sync_uses_the_sprint_wiql_when_current_iteration_resolves() -> None:
    client = _FakeDevOpsClient(current_iteration="widgets\\Sprint 1")
    await work_sync.sync_source(_FakeDB(), _source(), client)  # type: ignore[arg-type]
    assert client.wiql == (
        "SELECT [System.Id] FROM WorkItems WHERE [System.IterationPath] UNDER 'widgets\\Sprint 1' "
        "AND [System.State] NOT IN ('Closed','Removed','Done') ORDER BY [System.ChangedDate] DESC"
    )
    assert "@Me" not in client.wiql


async def test_sync_escapes_single_quotes_in_the_iteration_path() -> None:
    client = _FakeDevOpsClient(current_iteration="widgets\\O'Brien Sprint")
    await work_sync.sync_source(_FakeDB(), _source(), client)  # type: ignore[arg-type]
    assert "UNDER 'widgets\\O''Brien Sprint'" in client.wiql
    assert "AND [System.State] NOT IN ('Closed','Removed','Done')" in client.wiql


async def test_sync_query_wiql_wins_even_when_an_iteration_resolves() -> None:
    custom = "SELECT [System.Id] FROM WorkItems WHERE [System.State] = 'Active'"
    client = _FakeDevOpsClient(current_iteration="widgets\\Sprint 1")
    source = _source(query_wiql=custom)
    await work_sync.sync_source(_FakeDB(), source, client)  # type: ignore[arg-type]
    assert client.wiql == custom


async def test_sync_stores_the_owner_display_name_on_the_source() -> None:
    source = _source()
    client = _FakeDevOpsClient(owner_name="Felix De Lille")
    await work_sync.sync_source(_FakeDB(), source, client)  # type: ignore[arg-type]
    assert source.owner_display_name == "Felix De Lille"


async def test_sync_keeps_the_last_known_owner_when_the_lookup_fails() -> None:
    source = _source(owner_display_name="Felix De Lille")
    client = _FakeDevOpsClient(owner_fails=True)
    await work_sync.sync_source(_FakeDB(), source, client)  # type: ignore[arg-type]
    assert source.owner_display_name == "Felix De Lille"


def test_parse_assigned_to_keeps_the_display_name_only() -> None:
    identity = {"displayName": "Felix De Lille", "uniqueName": "felix@example.com"}
    assert work_sync.parse_assigned_to(identity) == "Felix De Lille"


def test_parse_assigned_to_missing_or_malformed_is_none() -> None:
    assert work_sync.parse_assigned_to(None) is None
    assert work_sync.parse_assigned_to("Felix") is None
    assert work_sync.parse_assigned_to({"displayName": ""}) is None


def test_html_to_markdown_handles_headings_lists_bold_and_links() -> None:
    html = (
        "<h2>Repro steps</h2>"
        "<p>Click <b>Submit</b> then "
        '<a href="https://example.com">see the log</a>.</p>'
        "<ul><li>First step</li><li>Second step</li></ul>"
    )

    md = work_sync.html_to_markdown(html)

    assert md.startswith("## Repro steps")
    assert "**Submit**" in md
    assert "[see the log](https://example.com)" in md
    assert "First step" in md
    assert "Second step" in md


def test_html_to_markdown_converts_br_to_a_line_break() -> None:
    md = work_sync.html_to_markdown("<p>Line one.<br>Line two.</p>")
    assert "Line one." in md
    assert "Line two." in md


def test_html_to_markdown_empty_or_none_is_empty_string() -> None:
    assert work_sync.html_to_markdown(None) == ""
    assert work_sync.html_to_markdown("") == ""


def test_parse_tags_splits_strips_and_drops_empties() -> None:
    assert work_sync.parse_tags("api; backend") == ["api", "backend"]
    assert work_sync.parse_tags("api;;  backend ;") == ["api", "backend"]


def test_parse_tags_none_or_empty_is_none() -> None:
    assert work_sync.parse_tags(None) is None
    assert work_sync.parse_tags("") is None
