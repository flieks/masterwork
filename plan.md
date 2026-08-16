# Plan — widen the work sync to the whole sprint, give @Me a real identity

## The change, in one paragraph

Today `sync_source` always sends `DEFAULT_WIQL` (`[System.AssignedTo] = @Me`), so the local
mirror only ever holds the PAT owner's items and the frontend's "Everyone" assignee filter
has nothing extra to show. This change resolves the team's current iteration path *first*,
and when one comes back, queries the whole sprint (`[System.IterationPath] UNDER '<path>'`,
every assignee, non-closed states) instead; with no current iteration it falls back to the
existing assigned-to-me WIQL, and an operator-set `source.query_wiql` still beats both. The
iteration path is external data interpolated into a query string, so it is escaped by
doubling single quotes and the module docstring's "no interpolation ever" rule is rewritten
to describe the single interpolation that is now allowed and why it is safe. Because the
sprint query drags in other people's items, "@Me" in the UI can no longer mean
"`!pulled_as_parent`" — so the backend learns the PAT owner's display name from a new
read-only `GET /_apis/connectionData` call, persists it on the work source
(`owner_display_name`, new nullable column + migration + response schema), and the frontend
matches `item.assigned_to` against that name, per source. Sprint dropdown, search box and
tree building are untouched.

## Files to add or change

### Backend — sync

**`backend/app/services/work_sync.py`** (change)

1. Module docstring: keep the "untrusted payload is stored/rendered, never executed" para,
   and replace the last sentence (`no item field is ever concatenated into a query`) with a
   description of the one interpolation now allowed: the team's current iteration path from
   `get_current_iteration_path()` is interpolated into the sprint WIQL, single-quote-escaped
   by doubling, and WIQL has no comment/statement-separator syntax to break out into — no
   *work-item field* is ever concatenated into a query.
2. Add next to `DEFAULT_WIQL`:
   ```python
   SPRINT_WIQL = (
       "SELECT [System.Id] FROM WorkItems WHERE [System.IterationPath] UNDER '{path}' "
       "AND [System.State] NOT IN ('Closed','Removed','Done') "
       "ORDER BY [System.ChangedDate] DESC"
   )
   ```
   plus a small helper, e.g.
   ```python
   def _sprint_wiql(path: str) -> str:
       """WIQL string literals escape a quote by doubling it."""
       return SPRINT_WIQL.format(path=path.replace("'", "''"))
   ```
   (Exact constant/helper names are the builder's call; keep them module-level and
   importable so tests can assert against them rather than re-typing the SQL.)
3. In `sync_source`, **move the best-effort iteration refresh from the end of the function to
   the top**, before `client.query_work_item_ids(...)`:
   ```python
   # Sprint lookup is best-effort; on failure keep the last known value.
   with contextlib.suppress(AzureDevOpsError):
       source.current_iteration = await client.get_current_iteration_path()
   ```
   Behaviour to preserve exactly: a *successful* call that returns `None` still clears
   `current_iteration`; only a raised `AzureDevOpsError` keeps the previous value.
4. WIQL selection becomes: `source.query_wiql` if set → else `_sprint_wiql(current_iteration)`
   if `source.current_iteration` is a non-empty string → else `DEFAULT_WIQL`.
5. Add a second best-effort refresh alongside the iteration one:
   ```python
   with contextlib.suppress(AzureDevOpsError):
       source.owner_display_name = await client.get_authenticated_user_display_name()
   ```
   Same shape as the iteration refresh: a raised error keeps the last known value.
6. Leave `pulled_as_parent`, the parent-id collection and the second parent-fetch pass
   exactly as they are — parents outside the sprint still have to be pulled for tree
   grouping. Leave `FIELDS`, the upsert and `SyncCounts` alone.
7. Update the `sync_source` docstring's first line ("Run the source's WIQL (or the default)")
   to name the three-way choice.

### Backend — provider

**`backend/app/providers/azuredevops.py`** (change) — one new read-only method, in the same
shape as `get_current_iteration_path`:

```python
async def get_authenticated_user_display_name(self) -> str | None:
    """The PAT owner's display name, or None when the org does not report one."""
    url = f"{self._org_url}/_apis/connectionData?api-version={API_VERSION}"
    async with self._client() as client:
        response = await client.get(url)
    user = _read_json(response).get("authenticatedUser")
    name = user.get("providerDisplayName") if isinstance(user, dict) else None
    return name if isinstance(name, str) and name else None
```

Org-level URL (no project segment), `client.get` only. The docstring's HARD RULE block stays
as written — this adds no POST, so "the only two POSTs" remains true — and
`tests/unit/test_azuredevops_client.py::test_module_has_no_write_methods` (greps for
`.patch(`, `.put(`, `.delete(`) stays green.

### Backend — persistence

**`backend/alembic/versions/0025_work_source_owner.py`** (add)

```python
revision: str = "0025_work_source_owner"
down_revision: str | None = "0024_dismissed_runs"

def upgrade() -> None:
    op.add_column("work_sources", sa.Column("owner_display_name", sa.Text(), nullable=True))

def downgrade() -> None:
    op.drop_column("work_sources", "owner_display_name")
```

The request said "next revision after `0022_session_launch_run_id`", but that is no longer
head: the chain is linear and runs `…0022 → 0023_coding_context_samples →
0024_dismissed_runs`. Chaining from `0024_dismissed_runs` is the only correct reading — a
new revision must descend from head or `alembic upgrade head` reports multiple heads. Follow
the file style of `0021_work_assignee_and_current_sprint.py` (module docstring with
`Revision ID` / `Revises` / `Create Date: 2026-08-16`, `from __future__ import annotations`,
`branch_labels`/`depends_on` set to `None`).

**`backend/app/db/models/work.py`** (change) — on `WorkSource`, next to `current_iteration`:

```python
# The PAT owner's DevOps display name, refreshed on sync; what "@Me" matches on.
owner_display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
```

`Text` is already imported. Nothing in `repositories/work.py` changes: `create_source` never
sets it, `sync_source` writes it on a loaded row and the existing `db.flush()` / route-level
`db.commit()` persist it.

### Backend — contract

**`backend/app/api/v1/work/schemas.py`** (change) — add to the `WorkSource` response model
only, after `current_iteration`:

```python
owner_display_name: str | None = Field(
    ..., description="Display name of the PAT owner, refreshed on sync; what @Me matches."
)
```

Do **not** touch `WorkSourceCreateRequest` — the value is derived from the PAT, never
operator input.

**`backend/app/api/v1/work/serializers.py`** (change) — one line in
`work_source_to_schema`: `owner_display_name=source.owner_display_name,`.

`docs/API_CONTRACT.md` is deliberately **not** edited in this stage — the contract delta is
recorded under "Data / contract impact" below for the document stage to apply at the end of
the run.

### Frontend — generated client

**`frontend/openapi.json`** and **`frontend/src/api/generated/*`** (regenerate, never
hand-edit). The repo's documented procedure (CONTRIBUTING.md ~line 50, mirrored by the
`contract` job in `.github/workflows/ci.yml`), with the backend running on port 8008:

```bash
cd backend && uv run alembic upgrade head && uv run uvicorn app.main:app --port 8008 &
curl -s localhost:8008/openapi.json | python3 -m json.tool > frontend/openapi.json
cd frontend && npm run generate:api:local
rm -f src/api/generated/git_push.sh src/api/generated/.openapi-generator-ignore
```

CI diffs both, so the committed `openapi.json` must be the `json.tool`-formatted body and
the client must be the untouched generator output. Expected delta: one
`'owner_display_name': string | null;` member on the `WorkSource` interface in
`src/api/generated/api.ts` (note the generator drops descriptions on `anyOf`-nullable
fields, as it already does for `current_iteration` — that is not drift).

### Frontend — filter semantics

**`frontend/src/features/work/tree.ts`** (change)

- Rewrite the `ASSIGNEE_ME` doc comment: it currently explains the `pulled_as_parent` trick,
  which the sprint-wide sync retires. New meaning: matches items whose `assigned_to` equals
  the owning source's `owner_display_name`.
- Add an exported type for the lookup, keyed by source id because items from different
  sources have different owners:
  ```ts
  /** `owner_display_name` per work-source id; a source with no owner yet matches nobody. */
  export type OwnerNames = Record<string, string | null>;
  ```
- `filterWorkItemTree(nodes, filters, owners: OwnerNames)` — third parameter, required (one
  call site), threaded into `matchesAssignee(item, filters.assignee, owners)`.
- ```ts
  function matchesAssignee(item: WorkItem, assignee: string | null, owners: OwnerNames): boolean {
    if (assignee === null) return true;
    if (assignee === ASSIGNEE_ME) {
      const owner = owners[item.source_id];
      return !!owner && item.assigned_to === owner;
    }
    return item.assigned_to === assignee;
  }
  ```
  An item whose source has no `owner_display_name` (null/absent/empty) must not match @Me.
- Leave `buildWorkItemTree`, `hasActiveFilters`, `countNodes`, `sprintOptions`,
  `assigneeOptions`, `sprintLabel` untouched. Re-export `OwnerNames` from
  `frontend/src/features/work/queries.ts` alongside the other `./tree` re-exports if the page
  imports the type from there (the page already imports every tree symbol via `../queries`).

**`frontend/src/features/work/components/WorkBacklogPage.tsx`** (change) — it already holds
`sources` from `workSourcesQueryAtom`:

```ts
const owners = useMemo<OwnerNames>(
  () => Object.fromEntries((sources.data ?? []).map((s) => [s.id, s.owner_display_name])),
  [sources.data],
);
const visible = useMemo(() => filterWorkItemTree(tree, filters, owners), [tree, filters, owners]);
```

Nothing else on the page changes.

**`frontend/src/features/work/components/WorkFilters.tsx`** (optional, one line) — the inline
comment above the `@Me` option ("Matches the rows the source's own (assigned-to-me) query
returned") describes the retired trick. Correct it to one line about matching the source
owner. No markup or props change; the dropdown, sprint select and search box stay as-is.

## Data / contract impact

- **Schema:** `work_sources.owner_display_name text NULL` (revision `0025_work_source_owner`,
  down-revision `0024_dismissed_runs`). Additive and nullable — existing rows read `NULL`
  until their next sync, and `downgrade()` drops the column. No data backfill, no destructive
  step.
- **API contract (for the document stage to write into `docs/API_CONTRACT.md`, not this
  stage):** the `WorkSource` **response** object gains `owner_display_name: string | null` —
  display name of the PAT owner, refreshed on every sync, null until the first successful
  sync or when DevOps reports none. `WorkSourceCreateRequest` is unchanged: the field is
  derived from the PAT and is never accepted as input. No endpoint, path, status code or
  request body changes anywhere.
- **Behavioural contract:** `POST /api/v1/work/sources/{id}/sync` now mirrors the *whole
  current sprint* (all assignees, states other than Closed/Removed/Done) when the team
  reports a current iteration, instead of only the PAT owner's items — so
  `GET /api/v1/work/items` returns strictly more rows for a source with an active sprint, and
  fewer duplicated `pulled_as_parent` rows (a story inside the sprint now arrives through the
  query itself). With no current iteration the behaviour is byte-identical to today.
- **Outbound calls:** one extra read-only request per sync,
  `GET {org_url}/_apis/connectionData?api-version=7.1`. Still zero writes to DevOps.

## Test strategy

**`backend/tests/integration/test_work.py`** (change) — the fake DevOps handler must grow
first, or *every* existing test in this file breaks:

- `_devops_handler` currently raises `AssertionError("unexpected request")` on any unknown
  path. Add a `/_apis/connectionData` branch returning
  `{"authenticatedUser": {"providerDisplayName": "Felix De Lille"}}`, parameterised so a test
  can make it fail (e.g. return `httpx.Response(500, ...)`) or omit the name.
- Capture the WIQL the handler receives (`json.loads(request.content)["query"]`) into a list
  the test can assert on, and make the WIQL branch honour it: for the sprint query, return
  the ids of the items whose `System.IterationPath` matches the current iteration (including
  one assigned to somebody else); for the assigned-to-me query keep today's `_WIQL_IDS`.
- Add an item to `_ITEMS` in `_CURRENT_ITERATION` assigned to a *different* person
  (e.g. `{"displayName": "Sam Owner"}`), reachable only via the sprint query.
- Existing count assertions (`{"fetched": 4, "inserted": 4, ...}`, `len(items) == 4`, the
  state/source filters, `pulled_as_parent is True` for 104) must be re-derived, not deleted:
  under the sprint query the mirror legitimately holds more rows. Note `_CURRENT_ITERATION`
  is `"widgets\\Sprint 1"` while `_ITEMS` iteration paths are `"Sprint 1"`/`"Sprint 2"` —
  align them (or make the handler's UNDER-match prefix-based) so the widened query really
  selects something.

New cases required:

1. Sprint resolves → the WIQL sent contains `[System.IterationPath] UNDER '<current
   iteration>'` and `NOT IN ('Closed','Removed','Done')`, does **not** contain `@Me`, and an
   item assigned to another person is persisted and returned by `GET /api/v1/work/items`.
2. Iteration lookup raises (`teamsettings/iterations` → 500) → the WIQL sent is exactly
   `DEFAULT_WIQL`; and the same when the lookup succeeds with `{"value": []}` (no sprint
   covering today).
3. An iteration path containing a single quote (e.g. `widgets\O'Brien Sprint`) → the WIQL
   contains `UNDER 'widgets\O''Brien Sprint'`, i.e. the doubled quote, and the query is not
   truncated/injected (assert the trailing `AND [System.State] NOT IN …` clause survives).
4. `owner_display_name` is persisted from `connectionData` (`GET /api/v1/work/sources`
   reports `"Felix De Lille"`), and a failing `connectionData` on a later sync keeps the
   previously stored value while the sync itself still succeeds.
5. An operator-set `query_wiql` still wins over the sprint query (cheap regression, add if
   the WIQL capture makes it a two-liner).

**`backend/tests/unit/test_work_sync.py`** (change, required for a green `pytest` even though
the request only names the integration file) — `_FakeDevOpsClient` needs a
`get_authenticated_user_display_name()` (plus a failure flag) or `sync_source` raises
`AttributeError`. `test_sync_uses_default_wiql_when_source_has_none` stays valid (that fake
reports no iteration); add unit coverage for the escaping helper if it is exposed
module-level.

**Frontend**

- `frontend/tests/components/harness/workFixtures.ts`: add `owner_display_name` to
  `workSource()` — default `'Alex Doe'`, the name `workItem()` already assigns — so the
  existing `@Me` expectations hold under the new semantics (item 4900 "Ingest reliability" is
  assigned to `'Sam Owner'` and so still drops out). Export the two names as constants
  (e.g. `OWNER_NAME`, `OTHER_ASSIGNEE`) and add a fixture for a *sprint* item assigned to
  another person that is **not** `pulled_as_parent` — the row only the widened sync can
  produce.
- `frontend/tests/components/workBacklogPage.ct.tsx`: rework
  `'@Me drops the rows pulled only as context for someone else'` so it proves the new rule —
  the dropped row must be excluded because `assigned_to !== owner_display_name`, not because
  of `pulled_as_parent`; the clearest form is an item with `pulled_as_parent: false` assigned
  to `'Sam Owner'` that @Me still hides. Add a case where the source has
  `owner_display_name: null` and @Me therefore matches nothing. Add the required "Everyone"
  case: with the sprint selected, the other person's non-context item is listed (and the
  count badge reflects it) while @Me hides it. Prefer passing these rows through the per-test
  `items:`/`sources:` overrides of `mockWork` rather than growing `workItemTree()`, so the
  row/count assertions in the ten other tests in this file stay valid.

**Gate before calling it done:** `cd backend && uv run ruff check . && uv run ruff format
--check . && uv run mypy app && uv run pytest`; `cd frontend && npm run typecheck && npm run
lint && npm run test:ct`; and the CI `contract` job's check reproduced locally — regenerated
`openapi.json` byte-identical to the committed one, `git diff --quiet -- src/api/generated`.

## Risks

- **Existing integration tests break silently-ish.** The new `connectionData` GET hits
  `_devops_handler`'s catch-all `AssertionError`, so *every* test using `_use_devops` fails
  until the handler is extended. Do that first.
- **Volume.** A sprint-wide query can return far more items than an assigned-to-me one;
  `get_work_items_batch` already chunks at 200, so the risk is UI/DB volume, not a failed
  call. The default sprint filter on the page keeps the view scoped.
- **`pulled_as_parent` now means less.** Items previously flagged as context can arrive
  through the sprint query as first-class rows (`pulled_as_parent=False`). The upsert
  overwrites the flag on the row, which is correct — but anything else keying off that flag
  (`WorkItemTable`'s "context" badge, the start-session suppression on context rows) will
  show fewer context rows after the change. That is the intended consequence; do not
  compensate for it.
- **Interpolation.** Escaping is a doubled single quote and nothing else. Do not build the
  WIQL by f-string at the call site: keep one helper so the escape can never be bypassed, and
  keep the docstring honest about it.
- **Iteration-path mismatch.** DevOps returns the *full* path
  (`widgets\2026 Q3.3`) from `get_current_iteration_path()`, while `System.IterationPath` on
  items is also the full path — `UNDER` is prefix-semantic, so this works; but the test
  fixtures currently mix `"widgets\\Sprint 1"` and `"Sprint 1"`, which will make a naive test
  pass for the wrong reason.
- **`owner_display_name` can be cleared.** A `connectionData` call that succeeds but reports
  no display name overwrites the stored value with `NULL` (only a raised `AzureDevOpsError`
  preserves it) — the exact same shape as `current_iteration` today, kept deliberately
  consistent; @Me then matches nothing for that source until the next good sync.
- **Contract drift.** `openapi.json` and the generated client are committed and CI diffs
  them; hand-editing either, or regenerating against a backend that has not run
  `alembic upgrade head`, fails the `contract` job.
