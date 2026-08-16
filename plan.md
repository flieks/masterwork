# Plan — sprint-wide work sync and a real identity behind `@Me`

## The change

Today `work_sync.sync_source` always asks DevOps for `DEFAULT_WIQL`
(`[System.AssignedTo] = @Me`), so the local mirror only ever holds the PAT
owner's own items and the work page's "Everyone" assignee filter has nothing
extra to show. This change resolves the team's current iteration **before**
querying and, when a path comes back, sends a sprint-scoped WIQL
(`[System.IterationPath] UNDER '<path>'`, every assignee) instead; with no
current sprint — or a failed lookup — it falls back to the unchanged
assigned-to-me WIQL, and an operator-set `source.query_wiql` still overrides
both. Because the mirror now contains other people's rows, `@Me` can no longer
mean "not `pulled_as_parent`": the DevOps client gains one read-only
`connectionData` GET that returns the PAT owner's display name, the name is
persisted on `work_sources.owner_display_name` (new nullable column, refreshed
best-effort each sync) and exposed on the `WorkSource` response schema, and the
frontend's `ASSIGNEE_ME` becomes `item.assigned_to === <that source's owner>`.
The iteration path is the first — and only — piece of external data ever
interpolated into a WIQL string, so it is escaped by doubling single quotes and
the module docstring's blanket "no interpolation" rule is rewritten to describe
exactly that one exception and why it is safe.

## Files to add or change

### Backend — sync and provider

| Path | Change | Why |
|---|---|---|
| `backend/app/services/work_sync.py` | Module docstring: replace "The only WIQL sent to DevOps is `DEFAULT_WIQL` or the operator-entered `source.query_wiql` — no item field is ever concatenated into a query" with the new rule: the team's current iteration path (from `teamsettings/iterations`, not from any work item) is interpolated into the sprint WIQL, single quotes doubled per WIQL's own escape; no work-item field is ever concatenated in. | The docstring is the file's stated invariant and would otherwise be a lie. |
| same | Add `_sprint_wiql(iteration_path: str) -> str` next to `DEFAULT_WIQL`: `escaped = iteration_path.replace("'", "''")`, then `SELECT [System.Id] FROM WorkItems WHERE [System.IterationPath] UNDER '<escaped>' AND [System.State] NOT IN ('Closed','Removed','Done') ORDER BY [System.ChangedDate] DESC`. | One place to build and escape the query; the test asserts on it directly. |
| same | In `sync_source`, move the sprint refresh from the bottom of the function to before `query_work_item_ids`, into a **local** variable: <br>`iteration: str \| None = None` <br>`with contextlib.suppress(AzureDevOpsError): iteration = await client.get_current_iteration_path(); source.current_iteration = iteration` <br>then `wiql = source.query_wiql or (_sprint_wiql(iteration) if iteration else DEFAULT_WIQL)`. | The local is what selects the WIQL, so a *failed* lookup falls back to `DEFAULT_WIQL` even when the column still holds a stale path from a previous run — while the "keep the last known value on failure" behaviour of the column itself is preserved (the assignment never runs when the call raises). |
| same | Add, in the same shape, right after it: `with contextlib.suppress(AzureDevOpsError): source.owner_display_name = await client.get_owner_display_name()`. | Refreshes the `@Me` identity each run; a raising call leaves the last known name in place. |
| same | Leave `pulled_as_parent`, the parent-id collection and the second `get_work_items_batch` pass byte-for-byte as they are. | Parents outside the sprint still have to be pulled for tree grouping. |
| `backend/app/providers/azuredevops.py` | Add `async def get_owner_display_name(self) -> str \| None` — `GET {org_url}/_apis/connectionData?api-version={API_VERSION}` via `self._client()`, read `authenticatedUser` from `_read_json(response)`, take `providerDisplayName`, `return name if isinstance(name, str) and name else None`. One-line docstring saying it answers "who is @Me". | The mirror needs the PAT owner's name; `connectionData` is the org-level read that gives it. Must stay a `client.get(...)` — the guard test `test_module_has_no_write_methods` greps this file for `.patch(` / `.put(` / `.delete(`. |

### Backend — persistence and contract

| Path | Change | Why |
|---|---|---|
| `backend/alembic/versions/0025_work_source_owner.py` *(new)* | `revision = "0025_work_source_owner"`, `down_revision = "0024_dismissed_runs"`; `upgrade` → `op.add_column("work_sources", sa.Column("owner_display_name", sa.Text(), nullable=True))`; `downgrade` → `op.drop_column(...)`. Copy the header/typing boilerplate from `0024_dismissed_runs.py`. | The request said "next after `0022_session_launch_run_id`", but 0023 and 0024 have landed since; chaining to the real head keeps `alembic upgrade head` single-headed (see Assumptions). Additive nullable column — no backfill, no lock concern on SQLite or Postgres. |
| `backend/app/db/models/work.py` | On `WorkSource`, after `current_iteration`: `owner_display_name: Mapped[str \| None] = mapped_column(Text, nullable=True)` with a one-line comment ("the PAT owner's display name, refreshed on sync — who `@Me` is"). `Text` is already imported. | Model and migration must agree on the type; tests build the schema from `Base.metadata`, so the model is what pytest exercises. |
| `backend/app/api/v1/work/schemas.py` | On the `WorkSource` response model, after `current_iteration`: `owner_display_name: str \| None = Field(..., description="Display name of the PAT owner — who \"@Me\" is for this source.")`. **Do not** touch `WorkSourceCreateRequest`. | Derived from the PAT, never operator input. Field order decides the property order in `openapi.json` and the generated client. |
| `backend/app/api/v1/work/serializers.py` | Add `owner_display_name=source.owner_display_name,` to `work_source_to_schema`, in the same position. | Serializers are explicit, not `from_attributes`. |

The repo's hand-written API contract doc under `docs/` enumerates the `WorkSource`
schema field by field, one line per field, immediately under `current_iteration`.
Adding the new field there is a one-line courtesy — nothing in CI enforces it —
and it was outside this stage's write boundary, so it is left to the build stage.

### Frontend

| Path | Change | Why |
|---|---|---|
| `frontend/openapi.json` | Regenerate from the running backend: `cd backend && uv run uvicorn app.main:app --port 8008` then `curl -s localhost:8008/openapi.json \| python3 -m json.tool > frontend/openapi.json` (CONTRIBUTING.md §"If you change the API"). Expected diff: one `owner_display_name` property on `WorkSource` (`anyOf` string/null + title/description) and one entry in its `required` list. | CI job "openapi contract is current" diffs this file against a live backend. |
| `frontend/src/api/generated/**` | `cd frontend && npm run generate:api:local`, then `rm -f src/api/generated/git_push.sh src/api/generated/.openapi-generator-ignore` (CI does the same before diffing). Expected diff: `'owner_display_name': string \| null;` on `interface WorkSource` in `api.ts`. Do not hand-edit unless the generator cannot run — see Risks. | CI fails if the committed client differs from a fresh generation. |
| `frontend/src/features/work/tree.ts` | Rewrite the `ASSIGNEE_ME` doc comment: it is the sentinel for "assigned to the PAT owner of the item's own source" — the sync now pulls the whole sprint, so `pulled_as_parent` no longer stands in for ownership. Extend the signature to `filterWorkItemTree(nodes, filters, ownerNames: ReadonlyMap<string, string \| null>)` (required third argument, no default) and thread it into `matchesAssignee(item, assignee, ownerNames)`: for `ASSIGNEE_ME`, `const owner = ownerNames.get(item.source_id) ?? null; return owner !== null && item.assigned_to === owner;`. Everything else (tree building, sprint/query matching, `assigneeOptions`, `sprintOptions`) untouched. | Keyed by source id because two sources have two different PAT owners; the explicit `owner !== null` guard is what stops a source with no `owner_display_name` yet from matching every unassigned item (`null === null`). |
| `frontend/src/features/work/components/WorkBacklogPage.tsx` | Add `const ownerNames = useMemo(() => new Map((sources.data ?? []).map((s) => [s.id, s.owner_display_name])), [sources.data]);` and pass it as the third argument in the `visible` memo (add to its dep array). No other change — sprint dropdown, search box, `WorkFilters`, `WorkItemTable` all stay as they are. | The sources query is already read on line 35 and used for `activeSprint`, so no new query or prop-drilling is needed. |
| `frontend/src/features/work/queries.ts` | No change — it re-exports `filterWorkItemTree` by name, and the signature change flows through. | Listed so the builder does not "helpfully" edit it. |

### Tests

| Path | Change |
|---|---|
| `backend/tests/integration/test_work.py` | (a) Extend `_devops_handler` with a `connectionData` branch returning `{"authenticatedUser": {"providerDisplayName": "Felix De Lille"}}` — **required**, the handler raises `AssertionError` on any unexpected path, so every existing sync test breaks without it. (b) Have the handler capture the WIQL body from the `wiql` branch (e.g. append `json.loads(request.content)["query"]` to a list closed over, or make `_use_devops` return it) and make the WIQL branch parameterisable per test for the iteration response (current path / empty `value` / raising 500 / quoted path). (c) New tests: *sprint-scoped sync sends the UNDER query and stores other people's items* — give one `_ITEMS` entry an `AssignedTo` display name other than "Felix De Lille", assert the sent WIQL contains `[System.IterationPath] UNDER 'widgets\Sprint 1'` and no `@Me`, and that the row lands with that `assigned_to`; *no current iteration → assigned-to-me fallback* (iterations returns `{"value": []}`, and a second case where it returns 500) asserting the sent WIQL is `work_sync.DEFAULT_WIQL`; *a path containing a single quote is escaped* (iterations returns `widgets\O'Brien Sprint`) asserting the sent WIQL contains `UNDER 'widgets\O''Brien Sprint'` and that the query is otherwise intact (still ends with the `ORDER BY`, still one `WHERE`); *owner_display_name is persisted* (`GET /api/v1/work/sources` shows "Felix De Lille") *and survives a failing connectionData call* (sync once, then make the branch return 500, sync again, assert the name is still there and the sync still returns 200). |
| `backend/tests/unit/test_work_sync.py` | **Required to stay green:** `_FakeDevOpsClient` needs `async def get_owner_display_name(self) -> str \| None` (an `AttributeError` is not an `AzureDevOpsError` and would not be suppressed). Give it the same `owner`/`owner_fails` constructor knobs as the iteration ones. `test_sync_uses_default_wiql_when_source_has_none` (fake returns no iteration) and `test_sync_uses_the_sources_own_wiql_verbatim_when_set` must keep passing unchanged; add a unit case that a fake reporting an iteration produces the `UNDER` query. |
| `frontend/tests/components/harness/workFixtures.ts` | Add `owner_display_name: 'Alex Doe'` to `workSource()` — required by the regenerated type, and it makes the default fixture's owner match `workItem()`'s default `assigned_to: 'Alex Doe'`, which is what keeps the existing `@Me` expectations (`a reload comes back with the remembered sprint and assignee`, count of 3) true under the new semantics. Optionally add a sprint-mate item assigned to `'Sam Owner'` for the new "Everyone" case rather than inlining it in the spec. |
| `frontend/tests/components/workBacklogPage.ct.tsx` | Rewrite `'@Me drops the rows pulled only as context for someone else'` so it discriminates on `assigned_to` vs the source's owner, not on `pulled_as_parent`: mount with a source whose `owner_display_name` is `'Alex Doe'` and two **non-context** items in the same sprint (Alex's story, Sam's task), select `@Me`, assert only Alex's row survives. Add a new test: with the same fixture and the assignee filter on "Everyone" (the default), both rows are listed — proving another person's sprint item now reaches the page. Also worth one case: a source with `owner_display_name: null` under `@Me` matches nothing (no accidental null-equals-null hit). Leave the sprint/search/persistence tests alone. |

## Data and contract impact

- **Schema**: one additive nullable column, `work_sources.owner_display_name` (TEXT). No backfill, no data migration, no index. Existing rows read `null` until their next sync; a `null` owner simply means `@Me` matches nothing for that source, which is the correct conservative behaviour.
- **API**: `WorkSource` responses gain a required-but-nullable `owner_display_name`. Additive for the frontend; `WorkSourceCreateRequest` is unchanged, so no client sends it.
- **Behavioural, not schema-level**: after this lands, `/api/v1/work/items` returns other people's items for any source whose team has a current sprint, and the mirror stops holding items assigned to the PAT owner that fall **outside** the current sprint (they are no longer re-synced; existing rows are not deleted — nothing in `sync_source` prunes). The sprint dropdown, which derives its options from the loaded items, will therefore narrow over time to the sprints actually pulled. That is the intended trade of "whole current sprint" over "all my items", and it is worth calling out in the PR description.
- **Outbound**: unchanged. The new provider method is a GET; the read-only guarantee stated in the API contract doc and in the module docstring still holds.

## Test strategy

Run the gate exactly as CONTRIBUTING.md documents it:

```bash
cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy app && uv run pytest
cd ../frontend && npm run typecheck && npm run lint && npm run build && npm run test:ct
```

Beyond that: `uv run alembic upgrade head` (and once back down) against a scratch
SQLite file to prove 0025 applies and reverses; `DATABASE_URL="sqlite+aiosqlite:///:memory:" uv run pytest`
is the default, and Postgres is worth a pass if one is to hand since CI runs both.
Backend integration tests own the WIQL-selection and escaping behaviour end to end
through the real HTTP endpoint with `httpx.MockTransport` (no live DevOps, per the
file's own docstring); the unit tests own WIQL *selection* without a DB. The
`test_module_has_no_write_methods` guard must be left untouched and observed to pass.
On the frontend, the Playwright component tests own the `@Me`/"Everyone" semantics;
`npm run typecheck` is what catches a fixture or caller that did not learn about the
new field or the new third argument.

## Risks

1. **The generator has to actually run.** CI regenerates `openapi.json` *and* the
   typescript-axios client and fails on any diff, so a hand-edit has to be
   byte-identical to generator output. `npm run generate:api:local` needs Java and
   the openapi-generator jar (`@openapitools/openapi-generator-cli` downloads it on
   first use); if that is unavailable offline, the fallback is a hand-edit matching
   the existing pattern for a nullable-with-description field — note that
   `current_iteration` (nullable + description) renders in `api.ts` with an **empty**
   doc-comment body, so `owner_display_name` must too. Prefer running the real
   generator; say so explicitly in the summary if it had to be hand-written.
2. **Migration numbering.** The request named 0022 as the parent; the real head is
   `0024_dismissed_runs`. Chaining to 0022 would create a second head and break
   `alembic upgrade head` in CI. Plan chains to 0024.
3. **The mock handler is a tripwire.** `_devops_handler` raises on any unrecognised
   path, so the `connectionData` branch is not optional — forgetting it fails every
   existing sync test with a confusing `AssertionError`, not a clean failure.
4. **Stale-iteration fallback.** Using `source.current_iteration` (rather than a
   local) to pick the WIQL would silently keep querying last month's sprint after a
   failed lookup. The local-variable shape above is the guard; a reviewer should
   check it survived.
5. **`@Me` matching a `null` owner.** If `matchesAssignee` compares
   `item.assigned_to === ownerNames.get(id)` without the explicit non-null guard,
   every unassigned item matches `@Me` on a source that has never synced. Covered by
   the suggested null-owner component test.
6. **Escaping is the security surface.** Doubling single quotes is WIQL's own escape
   and is sufficient for a quoted string literal; the value comes from DevOps'
   own iterations endpoint, not from a work item. Do not extend the interpolation to
   anything else, and keep the docstring narrow so the next change cannot cite it as
   precedent.
7. **Concurrent factory runs reset the worktree.** Check `--list-runs` before
   editing; a live run doing `git add -A` will sweep up in-progress edits.

## Assumptions

- New migration is `0025_work_source_owner`, chaining to `0024_dismissed_runs` (the
  actual head), not to `0022_session_launch_run_id` as literally requested.
- `owner_display_name` sits immediately after `current_iteration` in the model, the
  response schema and the serializer — field order is what drives the generated
  client's diff, and this keeps the two DevOps-derived fields together.
- The default component-test source owner is `'Alex Doe'`, matching the existing
  `workItem()` fixture, so the existing `@Me` assertions keep their meaning.
- `filterWorkItemTree`'s third parameter is required (no default `new Map()`), so a
  caller that forgets it fails typecheck rather than silently filtering nothing.
- The one-line `WorkSource` addition to the hand-written API contract doc is left
  to the build stage; nothing enforces it, but leaving it stale is contract drift.
