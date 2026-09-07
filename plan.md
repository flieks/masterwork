# Plan — read-only pull-request view + delegate a PR's comments to a coding session

## The change, in one paragraph

Masterwork already mirrors Azure DevOps work items read-only (`work_sources` →
`work_items`) and can spawn a real factory run (`POST /launcher/launch` →
`app/services/factory_launcher.spawn_factory_run`). This change adds the same
read-only treatment for pull requests and joins the two: three new mirror tables
(`work_pull_requests`, `work_pr_threads`, `work_repo_paths`), a PR sync that
reuses the two already-written-but-dead client reads
(`AzureDevOpsClient.list_active_prs` / `list_pr_threads`), five new endpoints
under `/api/v1/work`, and a "Pull requests" tab beside the existing backlog on
the work page. The headline behaviour is `delegatePullRequest`: it resolves the
PR's repository to a local working tree (stored mapping → scan of
`projects_root`'s immediate subfolders by their `.git/config` origin remote →
unresolved), and on a hit assembles a prompt from the PR plus its *unresolved*
threads and launches it as a real factory run, returning the launch id and run
id so the UI can link straight to it. Nothing in this run writes to Azure
DevOps and nothing pushes git: `app/providers/azuredevops.py` and its guard test
`backend/tests/unit/test_azuredevops_client.py` are not touched, the prompt
tells the delegated session in plain words that it must not push and must not
touch DevOps, and `app/services/work_outbound.py` stays the stub it is.

## Hard constraints this plan holds to

- `backend/app/providers/azuredevops.py` is **unchanged**. Both reads the feature
  needs already exist (`list_active_prs`, `list_pr_threads`), so no method is
  added, edited or removed there. `backend/tests/unit/test_azuredevops_client.py`
  is likewise unchanged — its `test_module_has_no_write_methods` grep and its
  `test_pr_endpoints_hit_the_documented_urls` already cover the two reads.
- No new `PATCH`/`PUT`/`DELETE` and no DevOps comment/work-item POST anywhere in
  the backend. The only DevOps traffic this change makes is
  `GET …/_apis/git/pullrequests` and
  `GET …/_apis/git/repositories/{id}/pullRequests/{id}/threads`.
- No git push, no `git` write of any kind. The repository scan **parses
  `.git/config`**; it never shells out to `git`.
- `docs/API_CONTRACT.md` is **not touched in this (plan) stage**. The build
  and/or document stage appends a new `# API Contract v1.40 — …` section after
  the current last section (`v1.39`, line 2987); every earlier section is FROZEN
  and must not be edited.

---

## Backend

### 1. Models — `backend/app/db/models/work.py` (modify)

Three tables added to the existing module, in the style `WorkItem` already sets
(explicit `mapped_column`, `JSONColumn` for raw payloads, `UTCDateTime` for
timestamps, `UniqueConstraint` named `uq_…`).

**`WorkPullRequest` → `work_pull_requests`**

| column | type | notes |
|---|---|---|
| `id` | `Integer` pk autoincrement | local id; what the URLs use |
| `source_id` | `Uuid` FK `work_sources.id` `ondelete="CASCADE"` | |
| `external_id` | `Integer` | DevOps `pullRequestId` |
| `repository_id` | `String(200)` | DevOps `repository.id` (a guid) |
| `repository_name` | `String(300)` | `repository.name` |
| `repository_remote_url` | `String(1000)` | `repository.remoteUrl` or `repository.webUrl` |
| `title` | `Text` | |
| `description` | `Text`, default `""` | DevOps sends plain text here, not HTML — stored verbatim, never rendered as HTML/markdown |
| `source_branch` | `String(500)` | `sourceRefName` with the `refs/heads/` prefix stripped |
| `target_branch` | `String(500)` | `targetRefName`, same strip |
| `status` | `String(50)` | `active`/`completed`/`abandoned` |
| `is_draft` | `Boolean`, server_default `false` | `isDraft` |
| `created_by` | `String(300)`, nullable | `createdBy.displayName` |
| `external_url` | `String(1000)` | built like `_work_item_url` does: `{org_url}/{project}/_git/{repository_name}/pullrequest/{external_id}` |
| `raw` | `JSONColumn` | the untouched payload |
| `external_changed_at` | `UTCDateTime` | |
| `synced_at` | `UTCDateTime` | |

`__table_args__`: `UniqueConstraint("source_id", "external_id",
name="uq_work_prs_source_external")`.

**`WorkPrThread` → `work_pr_threads`**

| column | type | notes |
|---|---|---|
| `id` | `Integer` pk | |
| `pull_request_id` | `Integer` FK `work_pull_requests.id` `ondelete="CASCADE"` | |
| `external_id` | `Integer` | DevOps thread id |
| `status` | `String(50)`, nullable | DevOps thread `status`; absent on some system threads |
| `is_resolved` | `Boolean`, server_default `false` | derived: `status` in `{fixed, closed, wontFix, byDesign}` |
| `file_path` | `String(1000)`, nullable | `threadContext.filePath`; null for a PR-level thread |
| `right_file_line` | `Integer`, nullable | `threadContext.rightFileStart.line`, falling back to `rightFileEnd.line` |
| `comments` | `JSONColumn` | list of `{id, author, content, comment_type, published_at}` in DevOps order |
| `raw` | `JSONColumn` | |
| `synced_at` | `UTCDateTime` | |

`__table_args__`: `UniqueConstraint("pull_request_id", "external_id",
name="uq_work_pr_threads_pr_external")`.

**`WorkRepoPath` → `work_repo_paths`** — the remote→folder memory.

| column | type | notes |
|---|---|---|
| `id` | `Integer` pk | |
| `remote_url` | `String(1000)`, unique (`uq_work_repo_paths_remote`) | the **normalized** remote; the key, never the repo name |
| `local_path` | `String(1000)` | absolute, validated to be an existing directory |
| `created_at` | `UTCDateTime`, `server_default=func.now()` | |

A module-level comment records *why* the key is the remote: the local folder is
frequently named something other than the repo.

### 2. Migration — `backend/alembic/versions/0028_work_pull_requests.py` (new)

`revision = "0028_work_pull_requests"`, `down_revision = "0027_coding_awaiting_input"`
(verified: `0027` is the real head — it chains from `0026_launch_checks_run` and
nothing chains from it). One migration, all three tables, written by hand in the
style of `0018_work_items.py` (explicit `sa.Column`, `JSONColumn` imported from
`app.db.types`, FKs with `ondelete="CASCADE"`, named unique constraints).
`downgrade()` drops the three in reverse dependency order
(`work_pr_threads` → `work_pull_requests` → `work_repo_paths`).

### 3. Remote-URL normalization + local-path resolution — `backend/app/services/repo_paths.py` (new)

A client-free leaf so it is unit-testable with no DB and no HTTP.

```python
def normalize_remote_url(url: str) -> str          # canonical comparison key
def read_git_origin(repo: Path) -> str | None      # parse repo/.git/config, no subprocess
async def resolve_local_path(db, remote_url, projects_root) -> Resolution
```

`normalize_remote_url` rules, applied in this order:

1. strip surrounding whitespace; return `""` for empty input.
2. drop any userinfo/PAT prefix — `https://user:token@host/…` and
   `https://org@dev.azure.com/…` both lose everything up to and including `@`
   in the authority.
3. rewrite scp-like ssh syntax (`git@host:path`) to `https://host/path`, and
   drop an explicit `ssh://` scheme the same way.
4. Azure DevOps ssh special case: host `ssh.dev.azure.com` (or
   `vs-ssh.*.visualstudio.com`) with a `v3/{org}/{project}/{repo}` path becomes
   `dev.azure.com/{org}/{project}/_git/{repo}` so the ssh and https forms of one
   repo compare equal.
5. lowercase the **host only** (the instruction's wording; path case is left
   alone so two genuinely different paths never collide).
6. drop a trailing `/`, then a trailing `.git`, then a trailing `/` again.

Returns `https://{host}{path}`. Documented in a short module docstring with the
one-line *why* (ssh and https clones of one DevOps repo must be one key).

`read_git_origin(repo)` reads `repo/.git/config` with `configparser`, looks for
the `[remote "origin"]` section's `url`, and returns `None` on any of: missing
file, unreadable file, parse error, no origin, no url. It never runs `git`.
(A `.git` *file* — a worktree/submodule pointer — is treated as "no origin"
rather than followed; noted in a comment.)

`resolve_local_path` implements the three steps in exactly the required order
and returns a small frozen dataclass `Resolution(local_path: Path | None,
matched_from: str, reason: str | None)` where `matched_from` is
`"stored"` / `"scan"` / `"none"`:

1. **stored** — `work_repo_paths` lookup on `normalize_remote_url(remote_url)`.
   Hit → that path. (If the stored path has since vanished from disk, it is
   treated as a miss and the scan runs, so a moved checkout self-heals.)
2. **scan** — for each immediate subdirectory of `projects_root` (sorted, skipping
   dotted names, matching `launcher_service.list_projects`' shape), read
   `read_git_origin(child)`, normalize it, compare. First match wins; the
   `(normalized remote, str(child))` pair is persisted into `work_repo_paths`
   and returned.
3. **none** — `local_path=None` with a human-readable `reason`, e.g.
   `"No folder under {projects_root} has {remote_url} as its git origin — pick
   the checkout for this repository."`

### 4. PR sync + prompt assembly — `backend/app/services/work_prs.py` (new)

Sits beside `work_sync.py` and reuses the same `AzureDevOpsClient` handed in by
the caller (the route injects `get_devops_client_factory`, tests inject a fake),
the same `SyncCounts` shape, and the same select-then-insert-or-update style.

- `sync_pull_requests(db, source, client) -> SyncCounts` — one
  `client.list_active_prs()` call for the whole source (bulk, as required), then
  upsert each payload on `(source_id, external_id)`. A payload without an int
  `pullRequestId` is skipped, exactly as `_upsert_payload` skips an id-less work
  item. Threads are **not** fetched here — that would be one HTTP call per open
  PR.
- `sync_pr_threads(db, pr, client) -> list[WorkPrThread]` — on-demand, for one
  PR: `client.list_pr_threads(pr.repository_id, pr.external_id)`, upsert each on
  `(pull_request_id, external_id)`, return the rows ordered by `external_id`.
  Re-running it updates rather than duplicates.
- `assemble_pr_prompt(pr, threads) -> str` — public (not `_`-prefixed) so the
  unit test can call it directly. Skips every `is_resolved` thread and every
  thread left with no comments. Shape:

  ```
  Pull request #{external_id}: {title}
  {external_url}
  Repository: {repository_name}
  Branch: {source_branch} -> {target_branch}

  Check out `{source_branch}` before doing anything else.

  ## Unresolved review comments

  ### {file_path}:{right_file_line}          (or "### On the pull request" when file_path is null)
  - {author}: {content}
  - {author}: {content}

  ## Rules
  - Address every comment above.
  - Run this repository's own checks before you finish.
  - Do NOT push. Do NOT create or update a branch on the remote.
  - Do NOT touch Azure DevOps: no comment replies, no thread resolution, no PR update.
  ```

  The module docstring states the security posture the way `work_sync.py` does:
  every PR/comment string is untrusted DevOps text, stored verbatim, placed into
  the prompt as data only, and handed to `spawn_factory_run` as a single
  **argv list element** — never a shell string, never `eval`'d, never
  interpolated into a query.

**Deliberate scope call:** `work_sync.sync_source` is *not* changed to also pull
PRs. PR sync is its own endpoint (`POST /work/sources/{source_id}/sync-prs`), so
a PR-sync failure structurally cannot destroy the work-item sync — the strongest
form of the "best-effort" requirement. Within PR sync itself, a per-PR payload
problem skips that PR instead of aborting the batch; a transport-level
`AzureDevOpsError` surfaces as `WorkSyncError` (502) exactly like
`syncWorkSource` already does. Recorded as an assumption.

### 5. Repository layer — `backend/app/repositories/work.py` (modify)

New functions in the existing file's style (`select`-then-insert-or-update, no
dialect-specific `ON CONFLICT`, so SQLite and Postgres behave identically):

`list_prs(db, *, source_id)`, `get_pr(db, pr_id)`, `upsert_pr(db, **fields) -> bool`,
`list_pr_threads(db, pull_request_id)`, `upsert_pr_thread(db, **fields) -> bool`,
`get_repo_path(db, remote_url)`, `upsert_repo_path(db, *, remote_url, local_path)`.

### 6. Schemas — `backend/app/api/v1/work/schemas.py` (modify)

`WorkPullRequest`, `WorkPrThreadComment`, `WorkPrThread`, `WorkRepoPath`,
`WorkRepoPathCreateRequest`, `PullRequestDelegateResponse`. Every field typed,
`Field(..., description=…)` where the name alone does not carry it (the frontend
client is generated from this).

```python
class PullRequestDelegateResponse(BaseModel):
    resolved: bool          # false => nothing was launched
    remote_url: str         # the PR's repository_remote_url, as stored
    local_path: str | None
    reason: str | None      # human-readable; set exactly when resolved is false
    launch_id: int | None
    run_id: str | None
    prompt: str | None      # what the launched session received
    unresolved_thread_count: int
```

`WorkSyncResult` is reused for `syncPullRequests` — same three counters, same
meaning.

### 7. Serializers — `backend/app/api/v1/work/serializers.py` (modify)

`work_pr_to_schema`, `work_pr_thread_to_schema`, `work_repo_path_to_schema`,
explicit field-by-field like the existing two (the `uuid.UUID` → `str` reason in
that module's docstring applies to `source_id` here too).

### 8. Service — `backend/app/api/v1/work/service.py` (modify)

```python
async def list_pull_requests(db, *, source_id: str | None) -> list[schemas.WorkPullRequest]
async def sync_pull_requests(db, source_id: str, client_factory) -> schemas.WorkSyncResult
async def list_pull_request_threads(db, pr_id: int, client_factory) -> list[schemas.WorkPrThread]
async def delegate_pull_request(db, pr_id: int, client_factory, spawner) -> schemas.PullRequestDelegateResponse
async def save_repo_path(db, body) -> schemas.WorkRepoPath
```

- `get_pr_or_404` mirrors `get_item_or_404`; a new
  `PullRequestNotFoundError(status_code=404)` goes in
  `backend/app/core/exceptions.py` beside `WorkItemNotFoundError`, and a
  `InvalidRepoPathError(status_code=400)` beside `InvalidBrowsePathError`.
- `list_pull_request_threads` loads the PR, builds the client from its source,
  calls `work_prs.sync_pr_threads`, commits, and returns the upserted rows —
  fetch-and-persist on read, as specified.
- `save_repo_path` validates `local_path`: `Path(value).expanduser()` must be
  absolute (else `InvalidRepoPathError`) and `.resolve()` must be an existing
  directory (else `InvalidRepoPathError`) — same shape as
  `launcher_service._validate_browse_path`, which it deliberately echoes. The
  remote is normalized before storing.
- `delegate_pull_request`:
  1. load the PR, refresh its threads from DevOps (so "unresolved" is current)
     via `work_prs.sync_pr_threads`.
  2. `projects_root = Path((await read_settings(db)).projects_root)` — read
     through the settings **service**, not `app.config` directly.
  3. `repo_paths.resolve_local_path(...)`. On `matched_from == "scan"` the
     discovered pair is persisted and **committed before the launch**, so a
     failed launch never loses the discovery.
  4. unresolved → return `resolved=False` with `remote_url` + `reason`,
     `launch_id`/`run_id`/`prompt` null. Nothing is spawned.
  5. resolved → `assemble_pr_prompt`, then delegate the launch to
     `app.api.v1.launcher.service.launch(db, LaunchRequest(project_path=…,
     request_text=prompt), spawner)`. That is the single existing spawn path:
     it allocates the run id, writes the `session_launches` row, refuses when
     the `claude` CLI is missing, and reads the run's own refusal back out of
     the log. Return its `id` and `run_id`.

  Cross-feature service import is already the house pattern
  (`launcher/service.py` imports `coding.service` and `settings.service`).

### 9. Routes — `backend/app/api/v1/work/routes.py` (modify)

Five routes, each with `response_model=`, on the existing `tags=["work"]` router:

| method + path | operation_id | response_model |
|---|---|---|
| `GET /work/prs` (`source_id` query, optional) | `listPullRequests` | `list[schemas.WorkPullRequest]` |
| `POST /work/sources/{source_id}/sync-prs` | `syncPullRequests` | `schemas.WorkSyncResult` |
| `GET /work/prs/{pr_id}/threads` | `listPullRequestThreads` | `list[schemas.WorkPrThread]` |
| `POST /work/prs/{pr_id}/delegate` | `delegatePullRequest` | `schemas.PullRequestDelegateResponse` |
| `POST /work/repo-paths` | `saveRepoPath` | `schemas.WorkRepoPath` (201) |

`{pr_id}` is the **local** `work_pull_requests.id` (an int), matching
`/work/items/{item_id}/start`. Routes only parse, inject
(`get_db`, `get_devops_client_factory`, `get_launch_spawner`) and shape — all
logic is in `service.py`.

---

## Frontend

### 10. Generated contract (regenerate, commit)

`make api` from the repo root (needs no running backend). Commit
`frontend/openapi.json` and the tracked files under
`frontend/src/api/generated/`. `make api-check` must be green.

### 11. `frontend/src/features/work/queries.ts` (modify)

Following the file's existing Jotai + jotai-tanstack-query conventions, and
`atomFamily` for the per-PR query (the established pattern — `sessions/queries.ts`,
`assets/queries.ts`, `chat/queries.ts`):

```ts
export const WORK_PRS_QUERY_KEY = ['workPullRequests'];
export const pullRequestsQueryAtom = atomWithQuery(...)                       // GET /work/prs
export const prThreadsQueryAtom = atomFamily((prId: number) => atomWithQuery(...))  // GET /work/prs/{id}/threads
export const syncPullRequestsMutationAtom = atomWithMutation(...)             // invalidates WORK_PRS_QUERY_KEY
export const delegatePullRequestMutationAtom = atomWithMutation(...)
export const saveRepoPathMutationAtom = atomWithMutation(...)
export function unresolvedThreadCount(threads): number
export function prBranchLabel(pr): string    // "feature/x → main"
```

`isHttpUrl` (already exported here) gates the `external_url` link, since PR URLs
are DevOps-supplied.

### 12. `frontend/src/features/work/components/WorkBacklogPage.tsx` (modify)

Adds a tab pair — **Backlog** / **Pull requests** — using
`~/components/ui/tabs` with the view in the URL (`?view=prs`), which is exactly
the `SessionsListPage` pattern; no new UI idiom. The tabs live inside the
existing "has at least one source" branch, so the empty-state
`NewWorkSourceForm` path is untouched. `WorkSourceBar` stays above both tabs.
The header count badge switches to the PR count on the PR tab.

### 13. `frontend/src/features/work/components/PullRequestList.tsx` (new)

One row per PR: `#{external_id}`, title, `repository_name`,
`source_branch → target_branch`, `created_by`, a `Draft` badge when `is_draft`,
an unresolved-comment count badge, an external link to `external_url`
(`target="_blank" rel="noreferrer noopener"`, only when `isHttpUrl`), a
**Sync pull requests** action (per source, in the tab header) and a
**Fix comments** action per row. Expanding a row mounts
`<PullRequestThreads prId={pr.id} />` — so the threads request only fires when
the user opens the PR.

### 14. `frontend/src/features/work/components/PullRequestThreads.tsx` (new)

Reads `prThreadsQueryAtom(prId)`. Groups threads by `file_path` with the
PR-level (`file_path === null`) group **first**, then file groups sorted by path;
within a group, threads keep their `external_id` order and each lists its
comments in order with author and time (`relativeTime`/`absoluteDateTime` from
`~/lib/datetime`, as `WorkSourceBar` does). A resolved thread is rendered but
de-emphasised (muted foreground, a "Resolved" badge) and collapsed by default
behind its own disclosure. Loading / error / empty states are explicit.

**Every PR, comment and branch string renders as plain text** — the same
treatment `StartPromptDialog.tsx` gives the assembled prompt (a `<pre
className="whitespace-pre-wrap break-words">` for comment bodies, plain
`{text}` children elsewhere). No `dangerouslySetInnerHTML`, no markdown
renderer, anywhere in these components.

### 15. Delegate + folder-picker fallback

`Fix comments` calls `delegatePullRequestMutationAtom`.

- `resolved === true` → `toast.success` with a link to the launched run
  (`/sessions?…`/the run link the sessions feature already uses for a
  `run_id`), built from `launch_id`/`run_id`.
- `resolved === false` → open the **existing** shared picker
  `~/components/FolderPickerDialog` (reused, not cloned), seeded with the
  response's `reason` as its description. On confirm: `saveRepoPath({remote_url,
  local_path})`, then **automatically retry** the delegate — so the user picks a
  folder once per remote, never twice.
- Any error → `toast.error` with `apiErrorMessage(err)`.

> Note: the request located the picker at `frontend/src/features/sessions
> FolderPicker`; it actually lives at `frontend/src/components/FolderPickerDialog.tsx`
> (the sessions feature is its *consumer*, via `LaunchSessionDialog.tsx`). That
> shared component is the one reused.

`frontend/src/features/work/index.ts` gains no new public export unless the
router needs one — the PR view is reached through `WorkBacklogPage`.

---

## Data / contract impact

- **Three new tables**, all additive. No existing table, column or constraint is
  altered; no data is rewritten or deleted. The migration is forward-only-safe
  and its `downgrade()` drops only what it created.
- **Five new endpoints**, all additive; no existing operation_id, path, request
  or response shape changes. `frontend/openapi.json` and
  `frontend/src/api/generated/*` gain the new operations and models and nothing
  else.
- **`docs/API_CONTRACT.md`**: a *new* `# API Contract v1.40 — …` section appended
  after v1.39, describing the five endpoints and the three tables. Written by the
  build/document stage, never by the plan stage; earlier sections are FROZEN.
- **Secrets**: unchanged. The PAT is still only ever *named* by
  `work_sources.secret_ref` and read at call time via `read_secret`.

## Test strategy

### Backend unit — `backend/tests/unit/test_repo_paths.py` (new)

- `normalize_remote_url`: ssh (`git@ssh.dev.azure.com:v3/acme/widgets/api`) and
  https (`https://dev.azure.com/acme/widgets/_git/api`) forms of one repo compare
  equal; trailing `.git` dropped; trailing slash dropped; embedded credentials
  (`https://user:pat@dev.azure.com/…` and `https://acme@dev.azure.com/…`)
  dropped; host case folded (`https://DEV.AZURE.COM/…`); a generic
  `git@github.com:org/repo.git` also normalizes; empty/garbage input does not
  raise.
- `read_git_origin`: a `tmp_path` repo with a real `.git/config` returns the
  origin; no `.git`, no `[remote "origin"]`, and a malformed config each return
  `None`. Asserted with no subprocess in play.
- `resolve_local_path`, all three steps, against the real test DB session:
  stored mapping wins and no scan happens; on a miss the scan over
  `projects_root`'s immediate children finds the repo by origin remote **and
  persists the pair**; nothing matches → `local_path is None` with a non-empty
  `reason`; a stored-but-vanished path falls through to the scan.

### Backend unit — `backend/tests/unit/test_work_prs.py` (new)

- `assemble_pr_prompt` includes the PR number, title, both branches and the
  checkout instruction; includes an unresolved thread's file path, line, and
  every comment with its author, in order; **omits** resolved threads entirely;
  handles a PR-level (null `file_path`) thread; and always states the
  no-push / no-DevOps rules. Also a case with zero unresolved threads.
- `sync_pull_requests` / `sync_pr_threads` field mapping against a
  `_FakeDevOpsClient` in the style `test_work_sync.py` already uses: the
  `refs/heads/` prefix is stripped from both branches, `is_resolved` is derived
  from each of `fixed`/`closed`/`wontFix`/`byDesign` (and *not* from `active`),
  `remoteUrl` is preferred over `webUrl` with a fallback when it is absent, and
  `threadContext` absence yields null `file_path`/`right_file_line`.

### Backend integration — `backend/tests/integration/test_work.py` (modify)

**Do this first, before anything else in this file.** Its `_devops_handler`
ends in `raise AssertionError(f"unexpected request: {request.url}")`, so the
moment any code path issues a PR request every existing test in the file breaks.
The handler learns two routes up front:

- `…/_apis/git/pullrequests` → a `{"value": [...]}` list of PR payloads;
- `…/_apis/git/repositories/{repository_id}/pullRequests/{pr_id}/threads` →
  that PR's threads (matched on the path, so a wrong repository id is visible
  in a test).

Both are driven by new module-level fixtures (`_PRS`, `_THREADS`) and
parameterised through `_use_devops(...)`/`_devops_handler(...)` the same way
`iteration_path`/`owner_name` already are, including a status-code override so a
failing PR call can be exercised. Existing assertions and counts are untouched.

New cases:

1. **sync then list** — `POST /work/sources/{id}/sync-prs` returns
   `{fetched, inserted, updated}`; `GET /work/prs` returns the rows with branches
   stripped of `refs/heads/`, `is_draft`, `created_by`, `repository_remote_url`
   and a built `external_url`; a second sync updates without duplicating; the
   `source_id` query scopes the list.
2. **threads on demand, idempotent** — no thread row exists after `sync-prs`
   alone (proving threads are not pulled per-PR on sync); `GET
   /work/prs/{id}/threads` fetches and upserts; calling it twice leaves the row
   count unchanged and reflects a changed upstream comment.
3. **delegate resolves via a stored mapping** — seed `work_repo_paths` through
   `POST /work/repo-paths`, then delegate: `resolved is true`, `local_path` is
   the stored one, a `run_id`/`launch_id` come back, and the fake spawner
   recorded exactly one call whose `request_text` contains the unresolved
   comment and the "must NOT push" / "must NOT touch Azure DevOps" lines and
   does **not** contain the resolved thread's text.
4. **delegate resolves via a `projects_root` scan and persists what it found** —
   `tmp_path` projects root with a folder whose `.git/config` origin is the PR's
   remote in the *other* URL form (ssh vs https) and whose folder name differs
   from the repo name; delegate resolves, and a subsequent `GET /work/prs` +
   second delegate uses the now-stored mapping (asserted by removing the folder's
   `.git/config` and delegating again successfully).
5. **delegate unresolved launches nothing** — no mapping, empty projects root:
   `resolved is false`, `remote_url` present, `reason` non-empty,
   `launch_id`/`run_id`/`prompt` null, and the fake spawner recorded **zero**
   calls.
6. **`saveRepoPath` rejects a relative path** (400) **and a non-existent path**
   (400); accepts an existing absolute directory and normalizes the remote
   before storing (posting the ssh form then the https form updates one row
   rather than creating two).

Fixtures this file needs, mirroring `tests/integration/test_launcher.py`:
a `_FakeSpawner` override of `get_launch_spawner` (no test forks a process) and
`monkeypatch.setattr(launcher_service, "RESUME_SETTLE_SECONDS", 0)` — without
the latter every delegate test pays a real 2-second sleep. A `projects_root`
fixture that `PATCH /api/v1/settings` points at a `tmp_path`.

### Frontend component — `frontend/tests/components/workPullRequests.ct.tsx` (new)

Playwright CT in the shape `workBacklogPage.ct.tsx` already sets (a
`page.route('**/api/v1/work/**')` handler with the CORS headers and OPTIONS
handling that file documents, mounted through `TestProviders`). New fixtures go
into the existing `frontend/tests/components/harness/workFixtures.ts`:
`pullRequest()`, `prThread()`, `prComment()`, `delegateResponse()`.

Cases:

1. **rows render** — switching to the Pull requests tab shows the number, title,
   repository, `source → target`, author, the draft badge and the
   unresolved-comment count, plus a link whose `href` is `external_url`.
2. **threads expand** — threads are only requested after the row is expanded;
   PR-level threads come first, then file-grouped ones with their path shown;
   comments render in order with author.
3. **a resolved thread is de-emphasised** — present, collapsed, carrying its
   "Resolved" marker, and not counted in the unresolved badge.
4. **folder-picker fallback fires** — delegate returns `resolved: false`, the
   picker opens, confirming a folder POSTs `/work/repo-paths` and the delegate is
   retried automatically; the second (resolved) response toasts and links to the
   run. Asserted on the recorded call list, the way that file asserts on
   `routes.calls`.

### Done gate

`ruff check` + `ruff format` on the touched backend files; `mypy` (strict);
`pytest` against the real test database; the Playwright component tests;
`make api-check` green; and a grep proving no new `.patch(`/`.put(`/`.delete(`
or DevOps comment POST exists anywhere under `backend/` (the existing
`test_module_has_no_write_methods` covers the provider file itself).

## Risks

1. **The integration-test trap, and it has bitten before.** `_devops_handler`
   raises `AssertionError` on any unrecognised path. If the two git routes are
   added to the handler *after* the PR code paths exist, the whole file
   (currently ~25 passing tests) turns red at once and the cause reads like a
   regression in work-item sync. Mitigation: extend the handler as the very
   first edit to that file, and run `pytest tests/integration/test_work.py`
   before writing any new test.
2. **The delegate launch reuses `launcher_service.launch`, which confines
   `project_path` to `projects_root`.** A repo path saved through
   `saveRepoPath` is only validated as "absolute + existing directory" (the
   request's own rule), so a user can store a checkout that lives outside
   `projects_root`; delegate will then fail with the launcher's existing
   `ProjectPathOutsideRootError` (400). This is consistent — the factory cannot
   run outside `projects_root` anyway — but it is a real, reachable 400 and the
   frontend must show its message rather than swallow it. Alternative rejected:
   bypassing `launch()` and calling `spawn_factory_run` directly, which would
   duplicate the run-id allocation, the missing-CLI refusal and the
   launch-row bookkeeping the request explicitly asked to reuse.
3. **`launch()` sleeps `RESUME_SETTLE_SECONDS` (2s).** Every delegate test must
   monkeypatch it to 0, as `test_launcher.py` does, or the suite gets slow and
   flaky-looking.
4. **Normalization is a guess about upstream URL shapes.** DevOps reports
   `repository.remoteUrl` as https while a developer's checkout may use ssh, and
   `.visualstudio.com` legacy hosts still exist. The rules above cover the forms
   this repo can reach, but an unlisted form (e.g. an on-prem TFS host) simply
   fails to match and falls through to the unresolved outcome — which is safe:
   the user picks the folder once and the mapping is stored. No silent
   mis-match, because the comparison is exact after normalization.
5. **`external_changed_at` for a PR.** The `pullrequests` list payload has
   `creationDate` but no dependable "last changed" field, so `creationDate` is
   used (falling back to sync time). PR ordering is therefore by creation, not
   by last activity. Called out so it is a decision and not a bug report later.
6. **Prompt injection surface.** The prompt is assembled entirely from
   DevOps-authored text. It is passed as one argv element to a `subprocess.Popen`
   list (never a shell string), and it is data inside the prompt, but a hostile
   PR comment could still try to talk the delegated session into doing something.
   The explicit no-push / no-DevOps rules are in the prompt, and phase 1 adds no
   DevOps write capability for such an instruction to reach — that is the actual
   containment. Worth stating in the v1.40 contract section.
7. **Scanning `projects_root` touches the filesystem on a request.** It is one
   `listdir` plus one small file read per immediate child, only on a cache miss,
   and the result is persisted — so the cost is paid once per remote. Reads are
   wrapped so an unreadable child is skipped rather than 500ing the endpoint.
