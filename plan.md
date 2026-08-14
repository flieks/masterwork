# Plan — Azure DevOps work-item integration v1 (read-only inbound)

## The change in one paragraph

Masterwork gains a **Work** surface: a mirror of the Azure DevOps work items assigned to
the user, pulled in over the DevOps REST API and never pushed back. Three new tables
(`work_sources`, `work_items`, `work_item_sessions`) hold a registered DevOps
org/project/team, the mirrored items, and the link between an item and a coding session
started for it. A new async httpx client (`app/providers/azuredevops.py`) speaks only the
four read endpoints it needs — WIQL query, work-item batch, active PRs, PR threads — and
contains **no** PATCH/PUT/DELETE/POST-comment method at all; the PAT is never stored,
only the *name* of the env var that holds it (`work_sources.secret_ref`). A sync service
runs the source's WIQL (or the assigned-to-me default), batch-fetches the returned ids in
chunks of 200, converts the HTML `Description` and `AcceptanceCriteria` fields to markdown
with `markdownify`, and upserts one mirror row per `(source_id, external_id)`. Outbound
stays a deliberate dead end: a dataclass describing a proposed action plus a guard that
raises `NotImplementedError`, called by nothing. Five endpoints under `/api/v1/work` list
sources, register a source, list items, sync a source, and assemble the session prompt for
one item. The frontend adds a **Work** page listing the mirrored backlog with a Sync button
per source and a Start-session button per item, backed by a regenerated OpenAPI client.

**The one thing the request assumed that the repo does not have: there is no reusable
session-launch path.** See "Session launch is deferred" below — the request's own fallback
branch applies, and it shapes two design decisions.

---

## Session launch is deferred — and why

The request says to launch a coding session "the same way existing session-launch code
does (find and reuse it; if no reusable launch path exists, return the assembled prompt in
the response with launch deferred and note it in the run report)". I looked; there is no
such path:

- `backend/app/services/claude_runner.py` is the *only* thing in the backend that spawns
  `claude`. It is deliberately read-only and sandboxed: `cwd=~/.claude`, `--allowedTools
  Read Glob Grep`, and an explicit `--disallowedTools Bash Edit MultiEdit Write
  NotebookEdit Task` with the comment "Read-only must be enforced by DENY". It cannot write
  code, so it is not a coding-session launcher.
- `backend/app/api/v1/coding/**` is pure *observability*: `coding_sessions` rows are created
  by the hook ingest (`POST /api/v1/hooks/events`) when an externally-started session
  reports its first event. Nothing in the backend creates a session row directly.
- `factory/run.py` is a CLI the user (or Claude Code) invokes from a terminal. Nothing in
  `backend/` shells out to it, and making an HTTP endpoint spawn a write-capable agent
  pipeline is a materially larger, security-relevant change than this request asked for.

So `POST /work/items/{id}/start` **assembles and returns the prompt with
`launched: false`** and does not spawn anything. Two consequences, both carried into the
schema below:

1. `work_item_sessions.session_id` is **nullable**. The request also requires a test
   covering "start endpoint prompt assembly **+ link row**", so the row must be written —
   and with launch deferred there is no `coding_sessions.id` to point at yet. A row with a
   null `session_id` means "a session was requested for this item, the prompt was handed
   out, nothing is bound to it yet". The FK still cascades once it is filled in.
2. The response carries the prompt so the UI can offer it for copy/paste.

---

## Files to add or change

### Backend — data

**`backend/app/db/models/work.py`** (new)
Three models, following `app/db/models/coding.py` (module docstring that explains *why* the
shape is what it is; module-level constants for the string enums; `Mapped[...]` +
`mapped_column`; portable `JSONColumn` / `UTCDateTime` from `app/db/types.py`).

- `WorkSource` — `id: uuid.UUID` pk `default=uuid.uuid4` (`Uuid`, as `Project` does),
  `provider: str` `String(50)` default `"azuredevops"` + `server_default`,
  `org_url: str` `String(500)`, `project: str` `String(200)`,
  `team: str | None` `String(200)`, `query_wiql: str | None` `Text`,
  `secret_ref: str` `String(200)` default `"AZURE_DEVOPS_PAT"` + `server_default` —
  with a comment stating it names an env var and that the PAT itself is never stored,
  `last_sync_at`, `created_at`, `updated_at` (`UTCDateTime`, `server_default=func.now()`,
  `onupdate=func.now()` on `updated_at`).
- `WorkItem` — `id: int` pk autoincrement, `source_id: uuid.UUID` FK
  `work_sources.id` ondelete CASCADE, `external_id: int`, `external_url: str` `String(1000)`,
  `item_type: str` `String(100)`, `title: str` `Text`, `description_md: str` `Text`
  default `""` + `server_default`, `acceptance_md: str | None` `Text`,
  `state: str` `String(100)`, `iteration: str | None` `String(500)`,
  `priority: int | None`, `tags: list[str] | None` `JSONColumn`, `raw: dict` `JSONColumn`,
  `external_changed_at: UTCDateTime`, `synced_at: UTCDateTime`.
  `__table_args__`: `UniqueConstraint("source_id", "external_id",
  name="uq_work_items_source_external")` plus `Index("ix_work_items_source_state",
  "source_id", "state")` — the list endpoint filters on exactly that pair.
- `WorkItemSession` — `id: int` pk, `work_item_id: int` FK `work_items.id` CASCADE,
  `session_id: str | None` `String(200)` FK `coding_sessions.id` CASCADE **nullable**
  (see above), `kind: str` `String(20)` (`KIND_SPAWNED = "spawned"` /
  `KIND_LINKED = "linked"`), `pushed_state: str | None` `String(100)`,
  `last_comment_at: UTCDateTime | None`, `created_at: UTCDateTime`.
  `pushed_state`/`last_comment_at` are outbound bookkeeping columns with nothing writing
  them in v1 — the model comment must say so rather than leaving them looking forgotten.

**`backend/app/db/base.py`** (edit) — add `from app.db.models import work as _work  # noqa`
to the existing import block, so `Base.metadata.create_all` (used by `tests/conftest.py`)
and Alembic autogenerate see the tables.

**`backend/alembic/versions/0018_work_items.py`** (new) — `revision = "0018_work_items"`,
`down_revision = "0017_coding_evidence"`. Hand-checked, in the style of
`0017_coding_evidence.py`: `sa.Uuid()` for the source pk/FK, `JSONColumn` imported from
`app.db.types`, `sa.DateTime(timezone=True)` with `server_default=sa.func.now()` for the
stamped timestamps, explicit `sa.ForeignKeyConstraint(..., ondelete="CASCADE")`, the unique
constraint and the index created by name, and a `downgrade()` that drops indexes then tables
in reverse order. Both dialects must take it (CI runs the suite on SQLite *and* Postgres);
this is a pure `create_table` revision so no batch mode is needed.

### Backend — DevOps client

**`backend/app/providers/azuredevops.py`** (new)
The requested path is kept, but note that `app/providers/` currently means *asset*
providers (the `Provider` Protocol in `providers/base.py`, registered explicitly in
`providers/registry.py`). This module is an external-API client, not a `Provider`, and it
is **not** added to `build_providers`. Its docstring must say that in one line so the next
reader does not go hunting for a `scan()`.

```
class AzureDevOpsError(DomainError-free plain Exception)   # translated in the service layer
class AzureDevOpsClient:
    def __init__(self, *, org_url, project, secret_ref, team=None, transport=None) -> None
    async def query_work_item_ids(self, wiql: str) -> list[int]
    async def get_work_items_batch(self, ids: Sequence[int], fields: Sequence[str]) -> list[dict]
    async def list_active_prs(self) -> list[dict]
    async def list_pr_threads(self, repository_id: str, pr_id: int) -> list[dict]
```

- One `httpx.AsyncClient` per call scope (`async with`), `timeout=30`, Basic auth built as
  `httpx.BasicAuth("", pat)` — DevOps takes an empty username and the PAT as password.
- The PAT is read via a new `app.config.read_secret(name)` accessor (below) using
  `secret_ref` as the variable name; a missing/empty value raises `AzureDevOpsError`
  naming the variable, e.g. `"AZURE_DEVOPS_PAT is not set"`. Never logged, never stored,
  never echoed into a response body.
- URLs exactly as specified:
  `POST {org_url}/{project}/_apis/wit/wiql?api-version=7.1`,
  `POST {org_url}/_apis/wit/workitemsbatch?api-version=7.1`,
  `GET {org_url}/{project}/_apis/git/pullrequests?searchCriteria.status=active&api-version=7.1`,
  `GET {org_url}/{project}/_apis/git/repositories/{repository_id}/pullRequests/{pr_id}/threads?api-version=7.1`.
- `get_work_items_batch` chunks at `BATCH_LIMIT = 200` ids and concatenates the results;
  an empty id list makes zero requests.
- **Hard rule, restated in the module docstring**: no method here issues PATCH, PUT,
  DELETE, or a comment/work-item-update POST. The only two POSTs are the WIQL query and the
  batch *read*, both of which are reads that happen to take a body. A reviewer should be
  able to grep this file for `patch`/`put`/`delete` and find nothing.
- `transport` is a plain constructor argument so tests pass `httpx.MockTransport` and no
  test ever reaches the network. No new test dependency (`respx` is not needed).
- Non-2xx → `AzureDevOpsError` with status and a truncated body; the response text is
  scrubbed of nothing else, so the service layer must not put it in a 500 verbatim (it
  wraps it in a `WorkSyncError` with a fixed prefix).

**`backend/app/config.py`** (edit) — add a module-level
`def read_secret(name: str) -> str | None` returning `os.environ.get(name) or None`, with a
one-line docstring: config stays the only module that touches the environment (house rule),
and `secret_ref` is a *dynamic* variable name so it cannot be a `Settings` field.

### Backend — services

**`backend/app/services/work_sync.py`** (new)

- `DEFAULT_WIQL` constant, one string, single-quoted state literals (the doubled quotes in
  the request are WIQL/SQL escaping in the requester's own quoting):
  `SELECT [System.Id] FROM WorkItems WHERE [System.AssignedTo] = @Me AND [System.State] NOT IN ('Closed','Removed','Done') ORDER BY [System.ChangedDate] DESC`
- `FIELDS` constant: `System.Title`, `System.Description`,
  `Microsoft.VSTS.Common.AcceptanceCriteria`, `System.State`, `System.IterationPath`,
  `System.WorkItemType`, `Microsoft.VSTS.Common.Priority`, `System.Tags`,
  `System.ChangedDate`.
- `html_to_markdown(html: str | None) -> str` — `markdownify(html, heading_style="ATX")`,
  whitespace-collapsed, `""` for None/empty. DevOps returns these two fields as HTML.
- `parse_tags(raw: str | None) -> list[str] | None` — DevOps sends `System.Tags` as a
  `"a; b; c"` string; split on `;`, strip, drop empties, `None` when absent.
- `async def sync_source(db, source, client) -> SyncCounts` — a frozen dataclass
  `SyncCounts(fetched, inserted, updated)`. Runs `source.query_wiql or DEFAULT_WIQL`,
  batch-fetches, maps each payload to the mirror columns, delegates the upsert to
  `repositories.work.upsert_item`, stamps `source.last_sync_at`, returns the counts.
- The whole DevOps payload goes into `raw` unchanged, and the docstring states the security
  posture in one line: **external content is untrusted data — stored, rendered as markdown,
  never executed, never `eval`'d, never interpolated into a shell command or a WIQL string.**
  The only WIQL that reaches the API is `DEFAULT_WIQL` or the operator-entered
  `source.query_wiql`; no item field is ever concatenated into a query.

**`backend/app/services/work_outbound.py`** (new) — stub only, ~30 lines.

```
OUTBOUND_REFUSAL = "outbound requires per-action user approval"

@dataclass(frozen=True)
class ProposedOutboundAction:
    kind: str            # ACTION_STATE_CHANGE | ACTION_COMMENT | ACTION_PR_LINK
    work_item_id: int
    summary: str
    payload: dict[str, Any]

def perform(action: ProposedOutboundAction) -> NoReturn:
    raise NotImplementedError(OUTBOUND_REFUSAL)
```

Module docstring: this exists to make the *shape* of a future outbound action reviewable
while guaranteeing nothing writes to DevOps today. Nothing imports `perform` outside its
own unit test.

### Backend — repository

**`backend/app/repositories/work.py`** (new) — module-level async functions taking
`db: AsyncSession` first, matching `repositories/projects.py` and `repositories/coding.py`
(no classes, no commits inside — the route/service commits, as `trigger_service.py` does).

```
create_source(db, *, org_url, project, team, query_wiql, secret_ref) -> WorkSource
list_sources(db) -> list[WorkSource]
get_source(db, source_id: uuid.UUID) -> WorkSource | None
list_items(db, *, source_id: uuid.UUID | None, state: str | None) -> list[WorkItem]
get_item(db, item_id: int) -> WorkItem | None
upsert_item(db, *, source_id, external_id, **fields) -> bool   # True when inserted
create_item_session(db, *, work_item_id, session_id, kind) -> WorkItemSession
```

`upsert_item` is a `select`-then-insert-or-update on `(source_id, external_id)` rather than
a dialect-specific `ON CONFLICT` — the repo must run identically on SQLite and Postgres, and
this is the pattern the coding repository already uses.

### Backend — API

**`backend/app/api/v1/work/{__init__,routes,schemas,service}.py`** (new)

> **Documented deviation from the request's literal path.** The request says
> `app/api/v1/work.py`. Every one of the eight existing v1 features is a *package*
> (`assets/`, `chat/`, `coding/`, `instructions/`, `observability/`, `projects/`,
> `proposals/`, `simulations/`) with `routes.py` + `schemas.py` + `service.py`, and the
> `backend-dev` house skill §4 mandates that layout. House conventions say to match the
> surrounding code, so the feature ships as a package. Everything else about the request's
> API spec — paths, methods, query params, behaviour — is unchanged. Also: there is no
> "v1 router" aggregator in this repo; `main.py` includes each feature router with
> `API_PREFIX`, so that is where the wiring goes.

`schemas.py` — Pydantic v2, `model_config = ConfigDict(from_attributes=True)` on the read
models, `Field(..., description=...)` on anything the generated TS client benefits from:
`WorkSource`, `WorkSourceCreateRequest`, `WorkItem`, `WorkSyncResult`
(`fetched/inserted/updated`), `WorkItemStartResponse` (`prompt: str`,
`launched: bool`, `session_id: str | None`, `link_id: int`).

`routes.py` — `router = APIRouter(tags=["work"])`, every route with `response_model=` and an
explicit `operation_id` (the TS method name):

| Method & path | operation_id | Notes |
|---|---|---|
| `GET /work/sources` | `listWorkSources` | `WorkSource[]`, newest first |
| `POST /work/sources` | `createWorkSource` | validates `org_url` matches `^https://dev\.azure\.com/[A-Za-z0-9._~-]+/?$`; 400 `InvalidWorkSourceError` otherwise |
| `GET /work/items?source_id=&state=` | `listWorkItems` | both filters optional |
| `POST /work/sources/{source_id}/sync` | `syncWorkSource` | `WorkSyncResult` |
| `POST /work/items/{item_id}/start` | `startWorkItem` | `WorkItemStartResponse` |

`service.py` — the layer that turns repository rows into schemas, raises the domain errors,
and owns the prompt assembly:

```
Title line:            "<item_type> #<external_id>: <title>"
                       "<external_url>"
"## Story"             description_md
"## Acceptance criteria"  acceptance_md      # section omitted entirely when absent/empty
```

`start_work_item` assembles the prompt, writes a `WorkItemSession(kind="spawned",
session_id=None)` row, and returns `launched=False` with the prompt (see "Session launch is
deferred").

**`backend/app/api/deps.py`** (edit) — `get_devops_client_factory()` returning a callable
`(WorkSource) -> AzureDevOpsClient`, following the existing `get_claude_runner` /
`get_light_runner` pattern so an integration test overrides it with a MockTransport-backed
client and no test ever touches the network. Added to `__all__`.

**`backend/app/core/exceptions.py`** (edit) — `WorkSourceNotFoundError` (404),
`WorkItemNotFoundError` (404), `InvalidWorkSourceError` (400),
`WorkSyncError` (502 — the DevOps call failed or the PAT env var is unset), each with the
one-line docstring the neighbours have.

**`backend/app/main.py`** (edit) — import `work.routes.router as work_router` and
`app.include_router(work_router, prefix=API_PREFIX)`, keeping the alphabetical order of the
existing block (after `simulations_router`).

### Backend — dependencies

**`backend/pyproject.toml` + `backend/uv.lock`** (edit, via `uv add`, never hand-edited)

- `httpx>=0.28` **moves from the dev group into runtime `dependencies`** — it is currently
  dev-only, and `app/providers/azuredevops.py` needs it at runtime. Keeping it in the dev
  group as well is harmless and lets the tests' existing pin stand.
- `markdownify>=0.13` added to runtime `dependencies` — required by the request, and the
  job (HTML → markdown for arbitrary DevOps rich text) is genuinely beyond the stdlib.
- CI runs `uv sync --frozen`, so `uv.lock` **must** be regenerated and committed in the same
  change or every backend job fails.
- mypy is `strict = true`: if `markdownify` ships no stubs, `ignore_missing_imports = true`
  is already set globally, so no per-module override should be needed — verify rather than
  assume.

### Frontend

**`frontend/openapi.json`** + **`frontend/src/api/generated/**`** (regenerated, committed)
Per `CONTRIBUTING.md` and the CI `contract` job, both the spec snapshot and the client are
committed and CI fails if either is stale. The flow, with the backend running on 8008:

```bash
cd backend && uv run uvicorn app.main:app --port 8008 &
curl -s localhost:8008/openapi.json | python3 -m json.tool > frontend/openapi.json
cd frontend && npm run generate:api:local
rm -f src/api/generated/git_push.sh src/api/generated/.openapi-generator-ignore
```

**`frontend/src/api/client.ts`** (edit) — import `WorkApi` from `./generated` and add
`work: new WorkApi(configuration, '', http)` to the `api` facade, in alphabetical position.

**`frontend/src/features/work/`** (new) — feature-folder layout like `features/sessions/`:

- `queries.ts` — `atomWithQuery` / `atomWithMutation` from `jotai-tanstack-query`, calling
  `api.work.*`, exactly as `features/observability/queries.ts` does.
  `workSourcesQueryAtom`, `workItemsQueryAtom` (keyed on the two filter atoms),
  `sourceFilterAtom` / `stateFilterAtom` (plain `atom`), `syncSourceMutationAtom` and
  `startWorkItemMutationAtom`, both invalidating the item/source query keys on success.
- `components/WorkPage.tsx` — the page shell copied in spirit from
  `features/projects/components/ProjectsListPage.tsx`: `max-w-6xl` container, header with
  title + count `Badge` + one-line description, explicit `isPending` skeleton /
  `isError` + `apiErrorMessage(error)` / empty-state / content branches using
  `~/components/EmptyState`, `~/components/ui/{button,badge,card,skeleton}`. The empty state
  says how to register a source (there is no create-source dialog in v1 — see assumptions).
- `components/WorkSourceBar.tsx` — one row per source: `org/project` (+ team),
  `last_sync_at` via `~/lib/datetime`, and a **Sync** button firing the sync mutation with a
  pending state and a `sonner` toast on success/failure.
- `components/WorkItemRow.tsx` — type badge, state badge, title, iteration, priority, an
  external link to `external_url` (`target="_blank" rel="noreferrer"`), and a **Start
  session** button. Because launch is deferred, success shows the returned prompt in a
  `Dialog` with a copy button and the plain sentence that the session is not started yet —
  the UI must not claim a run began that did not.
- `index.ts` — `export { WorkPage } from './components/WorkPage';`

**`frontend/src/app/router.tsx`** (edit) — `{ path: 'work', element: <WorkPage /> }`.
**`frontend/src/app/Layout.tsx`** (edit) — `{ to: '/work', label: 'Work', icon: ListTodo }`
in `NAV` (lucide-react is already a dependency), placed after Sessions.

### Docs

**`docs/API_CONTRACT.md`** (edit) — the five endpoints and the new schemas appended in the
existing table/schema style. The file is the repo's stated contract and already carries the
later coding/observability additions, so leaving it out would be drift.

---

## Data / contract impact

- **Additive only.** Three new tables, no column added to or removed from an existing table,
  no data backfill, no destructive step. `0018_work_items` is `create_table` ×3 + one unique
  constraint + one index; `downgrade()` drops exactly those.
- **`coding_sessions` is referenced, never modified** — `work_item_sessions.session_id` is a
  nullable FK with `ON DELETE CASCADE`, so deleting a run removes its link rows and nothing
  else. SQLite needs `foreign_keys=ON` for that cascade, which `app/db/session.py` already
  sets on every connection.
- **OpenAPI grows by five operations and five schemas** under a new `work` tag. Nothing
  existing changes shape, so the regenerated client is purely additive and no existing
  frontend call site moves.
- **No secret enters the database.** `work_sources.secret_ref` holds an env-var *name*; the
  PAT is read from the environment at call time and never persisted, logged, or returned.
- **Write direction: none.** After this change the repo contains no code path that mutates
  anything in Azure DevOps. `work_outbound.perform` raises before doing anything, and the
  client class has no write method to call.

---

## Test strategy

Existing framework and layout only: `pytest` with `asyncio_mode = "auto"`, unit tests under
`backend/tests/unit/`, integration tests under `backend/tests/integration/` against a real
throwaway database via the `client` fixture in `tests/conftest.py`. Frontend tests are
Playwright component tests under `frontend/tests/components/` using `TestProviders` and
`page.route('**/api/v1/**', …)` with the CORS-header helper those specs already share.

**Zero live DevOps calls, enforced structurally**: every test constructs
`AzureDevOpsClient(transport=httpx.MockTransport(handler))`, and the integration tests
override `get_devops_client_factory` in `app.dependency_overrides`. No new test dependency.

| File | Covers |
|---|---|
| `backend/tests/unit/test_azuredevops_client.py` (new) | **Chunking at 200**: 250 ids → exactly 2 POSTs to `workitemsbatch`, of 200 and 50, and the union is returned; 0 ids → 0 requests. WIQL POST hits `/{project}/_apis/wit/wiql?api-version=7.1` with the query in the body. Basic auth header is built from the env var named by `secret_ref`; a missing var raises `AzureDevOpsError` naming the variable and issues no request. Non-2xx → `AzureDevOpsError`. A guard test asserts the module source contains no `.patch(`/`.put(`/`.delete(` call — the hard rule, made mechanical. |
| `backend/tests/unit/test_work_sync.py` (new) | **HTML→markdown** of `System.Description` and `Microsoft.VSTS.Common.AcceptanceCriteria` (headings, lists, bold, links, `<br>`), empty/None → `""`/`None`. Tag string `"api; backend"` → `["api", "backend"]`. `DEFAULT_WIQL` used when `query_wiql` is null, and the source's own WIQL used verbatim when set. |
| `backend/tests/unit/test_work_outbound.py` (new) | `perform(...)` raises `NotImplementedError` with the exact message `outbound requires per-action user approval`, for each of the three action kinds. |
| `backend/tests/integration/test_work.py` (new) | **Sync upsert, both paths**: first sync of a source whose MockTransport returns 3 ids → `inserted=3, updated=0` and three mirror rows with the markdown-converted body; a second sync with one item's `System.Title`/`System.State`/`System.ChangedDate` changed → `inserted=0, updated=3` (or `1` updated + 2 no-op, whichever the implementation reports — the assertion is that the **row count stays 3**, the changed row carries the new title/state, and `synced_at` advanced). `last_sync_at` is stamped. **Start endpoint**: `POST /work/items/{id}/start` returns a prompt containing the title line, the `external_url`, `## Story` with the description markdown, and `## Acceptance criteria` — and a second item with no acceptance criteria returns a prompt with **no** `## Acceptance criteria` heading at all; a `work_item_sessions` row exists with `kind == "spawned"`; `launched is False`. **Source validation**: `POST /work/sources` with `https://example.com/foo` → 400, with `https://dev.azure.com/acme` → 201/200. **Filters**: `GET /work/items?state=Active` narrows, `?source_id=` scopes to one source. 404s for unknown source/item ids. |
| `frontend/tests/components/workPage.ct.tsx` (new) | Mount `<WorkPage />` inside `TestProviders` with `page.route` fulfilling `/work/sources` and `/work/items` from fixtures: the mocked items render with type badge, state, title, iteration and priority; clicking **Sync** issues `POST …/sync` (asserted on the recorded request URLs, the pattern `sessionsListPage.ct.tsx` uses); clicking **Start session** issues `POST …/start` and surfaces the returned prompt; the empty response renders the empty state, not a spinner. |

**Gate to run before claiming done** (matches CI):

```bash
cd backend && uv run alembic upgrade head && uv run ruff check . && uv run ruff format --check . \
  && uv run mypy app && uv run pytest -q
cd frontend && npm run typecheck && npm run lint && npm run build && npm run test:ct
```

---

## Risks

1. **The OpenAPI regeneration needs a JDK.** `npm run generate:api:local` runs
   `openapi-generator-cli`, which requires Java; the CI `contract` job installs Temurin 21
   for exactly this. If Java is unavailable in the build environment, the build stage must
   **say so and stop**, not hand-edit `src/api/generated/api.ts`. A hand-written client that
   differs by a byte from generator output fails the `contract` job on the next push and is
   worse than an honestly-reported gap. `frontend/openapi.json` itself needs no Java — it is
   `curl | python3 -m json.tool` — so regenerate it either way.
2. **`uv.lock` must be regenerated with `uv add`, not hand-edited.** CI runs
   `uv sync --frozen`; a lock that does not match `pyproject.toml` fails every backend job
   before a single test runs.
3. **Two dialects, one migration.** The suite runs on SQLite *and* Postgres in CI. `sa.Uuid`,
   `JSONColumn` and `UTCDateTime` absorb the differences, and the `upsert_item`
   select-then-write avoids `ON CONFLICT`; a dialect-specific shortcut would pass locally and
   fail half of CI.
4. **`markdownify` on hostile HTML.** DevOps rich text is user-authored and can contain
   `<script>`, `<img onerror=…>`, or a data-URI. `markdownify` strips tags rather than
   sanitising semantics, and the frontend renders through `MarkdownView` (react-markdown,
   which does not execute raw HTML by default). Keep it that way: do **not** enable
   `rehype-raw` for these fields, and never pass `description_md` through `dangerouslySetInnerHTML`.
5. **Prompt assembly ingests untrusted text.** The assembled prompt is built from a DevOps
   title/description that anyone with write access to that project can edit — including text
   that reads as instructions to an agent. v1 only *returns* the prompt to the operator, who
   decides whether to run it, which is the mitigation; if a later version auto-launches, that
   trust boundary needs its own review.
6. **PAT scope and expiry.** A stored memory notes the org PAT for the user's DevOps tenant
   has expired. Sync will 401 until a fresh PAT is exported as `AZURE_DEVOPS_PAT`; the
   `WorkSyncError` message must name the env var so that failure diagnoses itself instead of
   reading as a bug.
7. **`work_item_sessions` is written but never resolved in v1** — no code fills in a null
   `session_id`. That is the honest consequence of deferred launch, not an oversight, and the
   model comment must say so.
8. **`docs/API_CONTRACT.md` calls itself FROZEN.** It has been extended before (the coding and
   observability endpoints are in it), so appending is the established practice — but the
   additions must be appended, never a rewrite of the v1 sections.
