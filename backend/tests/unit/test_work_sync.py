"""HTML->markdown conversion, tag parsing, and WIQL selection — no network, no DB."""

from __future__ import annotations

from app.db.models.work import WorkSource
from app.services import work_sync


class _FakeDevOpsClient:
    """Records the WIQL it was asked, returns no ids — sync_source then makes
    no repository call at all, so this needs no real AsyncSession."""

    def __init__(self) -> None:
        self.wiql: str | None = None

    async def query_work_item_ids(self, wiql: str) -> list[int]:
        self.wiql = wiql
        return []

    async def get_work_items_batch(self, ids: list[int], fields: list[str]) -> list[dict]:
        return []


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
