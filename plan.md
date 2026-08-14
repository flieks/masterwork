# Plan — in-app session launcher (factory-only)

## The change in one paragraph

Add a launcher that starts a factory run from the Sessions screen. The backend
grows two new feature packages under `backend/app/api/v1/`: `settings`, a
one-row-per-key `app_settings` table exposed as `GET`/`PATCH /api/v1/settings`
and holding `projects_root` (the absolute folder all code projects live under,
defaulting to `~/Projects` expanded server-side); and `launcher`, which lists the
immediate subdirectories of that root (`GET /api/v1/launcher/projects`), creates
a new one from a validated name with `mkdir` + `git init`
(`POST /api/v1/launcher/projects`), and spawns a detached, fire-and-forget
`python3 <repo>/factory/run.py --repo <project_path> "<request_text>"`
(`POST /api/v1/launcher/launch`), persisting a `session_launches` row and
returning it with `launched: true`. Every path that reaches the filesystem is
resolved and checked to live inside `projects_root` with the existing
`resolve_within_roots` helper, so neither a traversal name nor a
`project_path` outside the root can escape. The frontend adds a **New session**
button on `SessionsListPage` opening a `LaunchSessionDialog` — project selector
fed by the projects endpoint with an inline "new folder" affordance, a required
request textarea, an autonomous/interview mode radio, and a small editable
projects-root field backed by the settings endpoints — and on success closes,
toasts, and lets the existing 2.5s poll surface the new session. Both modes
launch the same unattended run this iteration: `mode` is validated, stored and
displayed, and **no extra flag is passed to `run.py`**.

## Files to add or change

### Backend — persistence

| Path | Add/change | Why |
|---|---|---|
| `backend/app/db/models/app_settings.py` | add | `AppSetting` (key PK, value, updated_at) + `PROJECTS_ROOT_KEY = "projects_root"`. A key-value table, not a column-per-setting, so the next setting needs no migration. |
| `backend/app/db/models/launcher.py` | add | `SessionLaunch` (id, project_path, request_text, mode, launched_at, pid) + `MODE_AUTONOMOUS`/`MODE_INTERVIEW` constants, mirroring how `db/models/coding.py` keeps its vocabulary next to the table. |
| `backend/app/db/base.py` | change | Import both new modules alongside the existing six so `Base.metadata` (and therefore `create_all` in tests + Alembic autogenerate) sees them. |
| `backend/alembic/versions/0020_app_settings_and_launches.py` | add | `revision = "0020_app_settings_and_launches"`, `down_revision = "0019_work_item_parent"`. Creates `app_settings` and `session_launches`; `downgrade` drops both. Hand-written in the style of `0018_work_items.py` (explicit `sa.Column`s, `sa.DateTime(timezone=True)`, `server_default=sa.func.now()`), portable across SQLite and Postgres — no dialect-specific types. Run `uv run alembic heads` first and confirm a single head before writing it. |
| `backend/app/repositories/app_settings.py` | add | `get_value(db, key) -> str \| None` and `set_value(db, key, value) -> AppSetting` (select-then-insert-or-update, like `repositories/work.upsert_item`, so it runs identically on both dialects). No DB access outside a repository. |
| `backend/app/repositories/launcher.py` | add | `create_launch(db, *, project_path, request_text, mode) -> SessionLaunch` and `set_pid(db, launch, pid)`. |

### Backend — settings feature

`backend/app/api/v1/settings/{__init__.py,schemas.py,service.py,routes.py}` (all
new), following the `work` package layering exactly.

- `schemas.py`: `AppSettings { projects_root: str }` and
  `AppSettingsUpdateRequest { projects_root: str | None = None }` (None = leave
  unchanged), both with `Field(..., description=...)` so the generated TS is
  self-documenting.
- `service.py`: `read_settings(db)` returns the stored `projects_root` or, when
  unset, `str(settings.default_projects_root)` — the expansion happens here, so
  the API always hands out an absolute path. `update_settings(db, body)`
  expands `~`, requires the result to be absolute and an existing directory,
  raises `InvalidSettingError` otherwise, then writes through the repository and
  commits.
- `routes.py`: `router = APIRouter(tags=["settings"])`;
  `GET /settings` → `operation_id="getSettings"`,
  `PATCH /settings` → `operation_id="updateSettings"`, both
  `response_model=AppSettings`.

### Backend — launcher feature

`backend/app/api/v1/launcher/{__init__.py,schemas.py,service.py,routes.py}` (all new).

- `schemas.py`:
  - `class LaunchMode(StrEnum): AUTONOMOUS = "autonomous"; INTERVIEW = "interview"` —
    a `StrEnum` so the client generates a proper TS union rather than `string`.
  - `LauncherProject { name: str, path: str, is_git_repo: bool }`
  - `ProjectCreateRequest { name: str }`
  - `LaunchRequest { project_path: str, request_text: str, mode: LaunchMode = AUTONOMOUS }`
  - `SessionLaunchRead { id, project_path, request_text, mode, launched_at, pid, launched: bool }`
- `service.py` — all business logic, no FastAPI imports:
  - `list_projects(db)`: read `projects_root` via the settings service, list
    immediate subdirectories, skip non-directories and names starting with `.`,
    sort by name, set `is_git_repo = (p / ".git").exists()`.
  - `create_project(db, name)`: validate with `_validate_project_name` (below),
    join onto the root, re-check containment with
    `resolve_within_roots(candidate, [root])` (reused from
    `app/providers/base.py` — it already handles not-yet-existing tails and
    symlink escapes and is covered by `tests/unit/test_path_validation.py`),
    409 if it already exists, then `mkdir(parents=False)` and
    `git init` via `subprocess.run([...], cwd=path, check=True, capture_output=True)`.
    Returns the same `LauncherProject` shape as the list.
  - `_validate_project_name(name)`: reject empty/whitespace-only, anything
    containing `/`, `\`, or a NUL byte, `.`/`..`, any name starting with `.`,
    and anything over 100 chars → `InvalidProjectNameError`.
  - `launch(db, body, spawner)`: resolve `project_path` against the root with
    `resolve_within_roots`; reject with `ProjectPathOutsideRootError` when it is
    outside, is not a directory, or has no `.git` (`factory/run.py` exits 2 on a
    non-repo, and a detached process writing to `/dev/null` would fail
    invisibly — so this is caught synchronously, with a message that says so).
    Reject empty `request_text`. Insert the `session_launches` row and `flush()`
    to get its id, spawn, stamp `pid`, commit, return with `launched=True`.
    A spawn failure raises `LaunchFailedError` and the transaction is not
    committed.

- `routes.py`: `router = APIRouter(tags=["launcher"])` with
  `GET /launcher/projects` (`listLauncherProjects`, `list[LauncherProject]`),
  `POST /launcher/projects` (`createLauncherProject`, 201, `LauncherProject`),
  `POST /launcher/launch` (`launchSession`, `SessionLaunchRead`). The launch
  route injects the spawner via `Depends(get_launch_spawner)`.

### Backend — the spawn itself

`backend/app/services/factory_launcher.py` (new). One callable, injectable so
tests never fork:

```python
def spawn_factory_run(*, repo_root, python_bin, project_path, request_text, log_path) -> int
```

- argv is exactly
  `[python_bin, str(repo_root / "factory" / "run.py"), "--repo", str(project_path), request_text]`
  — a list, never a shell string, so `request_text` is never interpreted.
  **No mode flag**: interview behaviour ships separately.
- `subprocess.Popen(..., cwd=str(project_path), stdin=DEVNULL,
  stdout=log, stderr=STDOUT, start_new_session=True)`. `start_new_session=True`
  is what detaches the run from the request cycle and from uvicorn's process
  group, so reloading or Ctrl-C-ing the backend does not kill a live factory run.
  Nothing ever `wait()`s it.
- `log_path` is `settings.masterwork_home / "launches" / f"{launch_id}.log"`
  (directory created on demand) — derivable from the row id, so no extra column,
  and a run that dies at startup leaves a readable reason instead of nothing.
- Finished children are reaped opportunistically: the module keeps the `Popen`
  handles in a module-level list and `poll()`s them on each new launch, dropping
  the ones that have exited. Without this, a long-lived uvicorn accumulates
  zombies.

### Backend — wiring

| Path | Change |
|---|---|
| `backend/app/config.py` | Add `masterwork_repo_root: Path = Path(__file__).resolve().parents[2]` (`backend/app/config.py` → repo root), `factory_python: str = "python3"` (same shape as the existing `claude_bin`), and `default_projects_root: Path = Path.home() / "Projects"`. `config.py` stays the only module reading the environment. |
| `backend/app/core/exceptions.py` | Add `InvalidSettingError` (400), `InvalidProjectNameError` (400), `ProjectPathOutsideRootError` (400), `ProjectExistsError` (409), `LaunchFailedError` (502) — each a one-line `DomainError` subclass with a docstring, matching the existing file. |
| `backend/app/api/deps.py` | Add `get_launch_spawner() -> LaunchSpawner` (a `Callable` alias next to the existing `DevOpsClientFactory`), returning `factory_launcher.spawn_factory_run` bound to `settings.masterwork_repo_root` / `settings.factory_python`. Tests override it. Add both to `__all__`. |
| `backend/app/main.py` | Import and `include_router(launcher_router, prefix=API_PREFIX)` and `include_router(settings_router, prefix=API_PREFIX)`, keeping the alphabetical order of the existing block. |

### Frontend

| Path | Add/change | Why |
|---|---|---|
| `frontend/openapi.json`, `frontend/src/api/generated/**` | regenerate | `npm run generate:api:local` (per `docs/DEV_SETUP.md`) after refreshing `frontend/openapi.json` from a running backend at `:8008`. Both are committed in this repo. |
| `frontend/src/api/client.ts` | change | Add `LauncherApi` and `SettingsApi` to the imports and to the `api` facade (`launcher:`, `settings:`), same `new XApi(configuration, '', http)` shape as the other nine. |
| `frontend/src/features/sessions/queries.ts` | change | Add `launcherProjectsQueryAtom`, `appSettingsQueryAtom`, `createLauncherProjectMutationAtom`, `updateSettingsMutationAtom`, `launchSessionMutationAtom` — `atomWithQuery`/`atomWithMutation` over the generated client, with `onSuccess` invalidating `['launcherProjects']` / `['appSettings']` via `queryClientAtom`, exactly as `features/work/queries.ts` does. |
| `frontend/src/features/sessions/components/LaunchSessionDialog.tsx` | add | The dialog. Modelled on `features/projects/components/NewProjectDialog.tsx`: `Dialog`/`DialogContent`/`DialogHeader`/`DialogFooter` from `~/components/ui/dialog`, `Input`, `Textarea`, `Button`, `toast` from `~/components/ui/sonner`, errors through `apiErrorMessage`. Contents: (1) a projects-root `Input` with a **Save** button calling the settings mutation; (2) a project selector — a native `<select>` labelled *Project* fed by `launcherProjectsQueryAtom`, plus an inline "New folder" row (name `Input` + **Create** button) that calls the create mutation and selects the returned path; (3) a required `Textarea` labelled *Request*; (4) a mode radio in a `<fieldset>` with `role="radiogroup"` and two native `<input type="radio">`s — *Fully autonomous: plan and build with best-guess assumptions, never ask me* (default, value `autonomous`) and *Interview me: pause on weak assumptions before building* (value `interview`); (5) footer with Cancel + **Launch**, disabled until a project is selected and the request is non-empty. On success: `onOpenChange(false)`, reset, `toast.success('Factory run started', { description: <project path> })`. Native radios/select rather than new Radix packages — the repo has no `@radix-ui/react-radio-group` or `-select`, and the house rule is not to add a dependency the standard library and existing ones can cover. |
| `frontend/src/features/sessions/components/SessionsListPage.tsx` | change | A **New session** button in the page `<header>` row (right-aligned, `Plus` icon from `lucide-react`, which is already a dependency), holding `const [launchOpen, setLaunchOpen] = useState(false)` and rendering `<LaunchSessionDialog open={launchOpen} onOpenChange={setLaunchOpen} />`. In the header rather than inside the Runs tab so it is reachable from all three tabs. |
| `docs/API_CONTRACT.md` | change | Append a `## Session launcher` section in the same layout as the `work` one: the schema block, a *New endpoints* table (method & path, operation_id, request, response, error codes), and a *Behavior* section stating that both modes launch the same unattended run today, that no mode flag reaches `run.py`, and that attribution rides the existing `MASTERWORK_FACTORY_RUN_ID` handshake. Also update the deferred-launch note at `docs/API_CONTRACT.md:2238` to point at the new endpoint, since a reusable launch path now exists. |

## Data / contract impact

- **New tables.** `app_settings` (`key` `String(100)` PK, `value` `Text`,
  `updated_at`) and `session_launches` (`id` int PK autoincrement,
  `project_path` `Text`, `request_text` `Text`, `mode` `String(20)`,
  `launched_at` `UTCDateTime` default now, `pid` `Integer` nullable). Both are
  additive; migration `0020` on top of `0019_work_item_parent`. No existing
  table, column or row is touched, so the migration is reversible and safe on a
  populated database.
- **New OpenAPI surface** — five operations under two new tags, so the
  regenerated client gains `SettingsApi` and `LauncherApi`. `LaunchMode` is a
  `StrEnum`, so `mode` lands in TS as `'autonomous' | 'interview'`.
- **No change to the sessions contract.** The launched run attributes itself
  through `MASTERWORK_FACTORY_RUN_ID` / `MASTERWORK_FACTORY_STAGE`
  (`factory/adw/agent.py:18`, forwarded by
  `backend/app/observability/forwarders/claude_code.py:31`), so the new session
  arrives through the existing hook ingest and the existing 2.5s poll. Nothing
  in `app/api/v1/coding/` changes.
- **Filesystem.** New directory `~/.masterwork/launches/` for per-launch logs;
  project folders are created under `projects_root` only.

## Test strategy

Existing frameworks only — pytest + httpx `ASGITransport` for the backend,
Playwright component tests for the frontend.

**`backend/tests/unit/test_launcher_names.py`** (new) — `_validate_project_name`
in isolation: accepts `my-app`, `Deploy_pipeline`, rejects `""`, `"   "`,
`"../evil"`, `"a/b"`, `"a\\b"`, `"."`, `".."`, `".hidden"`, a 300-char name, and
a name with a NUL byte.

**`backend/tests/integration/test_settings.py`** (new) — `GET /api/v1/settings`
on an empty DB returns the expanded default; `PATCH` with `str(tmp_path)`
persists and a follow-up `GET` reads it back; `PATCH` with `{}` leaves it
unchanged; `PATCH` with a relative path and with a non-existent path both 400.

**`backend/tests/integration/test_launcher.py`** (new) — a fixture that PATCHes
`projects_root` to `tmp_path` (real endpoint, no new dependency to override) and
seeds `tmp_path/alpha` (with `.git`), `tmp_path/beta` (without), `tmp_path/.hidden`
and a plain file:
- `GET /launcher/projects` returns `alpha` and `beta` only, sorted, with
  `is_git_repo` true/false respectively.
- `POST /launcher/projects {"name": "gamma"}` → 201, the directory exists,
  `.git` exists (real `git init` in a temp dir — hermetic and fast), and the
  response says `is_git_repo: true`.
- `POST /launcher/projects` with `"../escape"` and with `"a/b"` → 400 and
  nothing is created outside `tmp_path`.
- `POST /launcher/projects {"name": "alpha"}` → 409.
- `POST /launcher/launch` with `app.dependency_overrides[get_launch_spawner]`
  set to a fake recording its kwargs and returning pid `4242`: asserts
  `launched: true`, `pid == 4242`, `mode == "autonomous"`, a `session_launches`
  row persisted, the argv is
  `["python3", "<repo>/factory/run.py", "--repo", "<tmp>/alpha", "<request>"]`
  and **carries no mode flag**, and cwd is the project path. **No real process
  is ever spawned.**
- `mode: "interview"` produces the identical argv and stores `interview`.
- Launch with a `project_path` outside `projects_root` (e.g. `tmp_path.parent`),
  with a `..` segment, with a file path, and with a non-git directory → 400, and
  the fake spawner was never called.
- `mode: "sideways"` → 422 from the enum.

**`frontend/tests/components/launchSessionDialog.ct.tsx`** (new) — mounts
`<LaunchSessionDialog open onOpenChange={() => {}} />` inside `TestProviders`,
routing `**/api/v1/**` with the CORS/OPTIONS helper copied from
`sessionsListPage.ct.tsx` (the generated client is cross-origin):
- the projects from the mocked list endpoint appear in the selector, and
  **Launch** is disabled until a request is typed;
- the mode radio defaults to *Fully autonomous* and selecting *Interview me*
  puts `"interview"` in the POSTed body;
- the new-folder affordance POSTs `/launcher/projects` and selects the returned
  path;
- **Launch** POSTs `/launcher/launch` with the selected `project_path` and the
  typed `request_text`, and the dialog reports the run started.

Gates the builder must clear: `uv run ruff check .`, `uv run ruff format --check .`,
`uv run mypy app`, `uv run pytest` in `backend/`; `npm run typecheck`,
`npm run lint`, `npm run test:ct` in `frontend/`.

## Risks

1. **A detached run that dies at startup is invisible.** `factory/run.py` exits
   2 for a missing/non-git repo, an unresolvable config, or a run branch that
   already exists, and the caller returns `launched: true` regardless. Mitigated
   two ways: the launch endpoint pre-checks directory-ness and `.git`
   synchronously, and stdout/stderr go to `~/.masterwork/launches/<id>.log`
   rather than `/dev/null`. It stays possible for a run to fail after the 200 —
   the sessions list is the source of truth, and the response never claims the
   run succeeded, only that it was started.
2. **Arbitrary-path write is the whole risk surface here.** Every path is
   funnelled through `resolve_within_roots(candidate, [projects_root])`, which
   already resists `..` and symlink escapes and is unit-tested. The name
   validator is a second, independent gate on `create`. Both must be tested
   negatively, not just positively.
3. **`request_text` is untrusted input handed to a subprocess.** It is passed as
   a single argv element to a `Popen` with no `shell=True` anywhere, so it is
   never interpreted. A builder that reaches for a shell string reintroduces
   command injection; the test asserting the argv list is the guard.
4. **Zombie accumulation.** Nothing waits on the child. The `poll()`-on-next-launch
   reaping keeps this bounded; without it a long-lived backend leaks a zombie
   per launch.
5. **`projects_root` is unauthenticated, like the rest of this backend.**
   Masterwork binds to localhost and has no auth layer, so `PATCH /settings`
   can repoint the root anywhere the backend user can read. This is consistent
   with the existing `/api/v1/instructions` endpoint (which writes
   `~/.claude/CLAUDE.md`) and is not widened here — but it does mean the
   containment check protects against mistakes, not against an attacker who can
   already call the API.
6. **Divergent Alembic heads.** `0019_work_item_parent` looks like the only
   head, but the builder must confirm with `alembic heads` before writing
   `0020`; two heads make `upgrade head` fail for everyone.
7. **Client regeneration needs a running backend.** `generate:api:local` reads
   the committed `frontend/openapi.json`, which must be refreshed from
   `http://localhost:8008/openapi.json` after the routes land, or the frontend
   compiles against a stale contract.
