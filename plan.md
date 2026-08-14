# Plan — server-side folder picker for the session launcher

## The change in one paragraph

Today the projects root can only be set by typing an absolute path into a text
field in the launch dialog (`LaunchSessionDialog.tsx`), and the only validation
feedback is a 400 toast from `PATCH /api/v1/settings`. This change adds a
read-only directory-browsing endpoint — `GET /api/v1/launcher/browse` with
`operation_id: browseDirectories` — that takes an optional absolute `path` query
param (defaulting to the current `projects_root` from settings) and returns the
resolved directory, its parent (`null` at the filesystem root), and its
non-hidden subdirectories as `{name, path}` sorted by name. It never returns
files and silently skips entries that raise `PermissionError`. Invalid input —
relative, nonexistent, or not a directory — raises a new `DomainError` subclass
that the existing `app.main` handler turns into a 400, exactly like
`_validate_projects_root` does for settings. On the frontend, a **Browse**
button beside the projects-root control opens a nested panel inside the dialog
that shows a clickable breadcrumb of the current directory, an up-one-level
button, the subdirectory list (clicking a row descends into it), and a **Use
this folder** button that saves the shown directory as `projects_root` through
the existing `updateSettingsMutationAtom` — whose `onSuccess` already
invalidates both `appSettingsQueryAtom` and `launcherProjectsQueryAtom`, so the
project dropdown repopulates from the new root. The panel gets the same
`Skeleton` / inline-error-with-Retry treatment the dialog already uses for its
two queries. The checked-in `frontend/openapi.json` and the generated
typescript-axios client are extended by hand (no shell in this run, same as the
v1.26 build), and `docs/API_CONTRACT.md` gains a v1.27 section.

Browsing any absolute directory is deliberately allowed: this is a local
single-user app and `PATCH /api/v1/settings` already accepts any absolute path,
so `resolve_within_roots` is **not** used here — that would make the picker
unable to leave the root it exists to change.

---

## Files to add or change

### Backend

| Path | Change | Why |
|---|---|---|
| `backend/app/core/exceptions.py` | Add `InvalidBrowsePathError(DomainError)` with `status_code = 400`, placed next to `InvalidSettingError` / `ProjectPathOutsideRootError`. | The service layer stays HTTP-agnostic; `app/main.py`'s `_domain_error_handler` already maps `DomainError` → `{"detail": ...}` at `exc.status_code`. A distinct class (rather than reusing `InvalidSettingError`) because a browse path is not a setting and the detail messages differ. |
| `backend/app/api/v1/launcher/schemas.py` | Add `DirectoryEntry {name: str, path: str}` and `DirectoryListing {path: str, parent: str \| None, entries: list[DirectoryEntry]}`, both with `Field(..., description=...)` on the non-obvious fields. | These become `DirectoryEntry` / `DirectoryListing` in the TS client; the frontend needs `parent` to drive the up-one-level button and `path` to seed the breadcrumb. |
| `backend/app/api/v1/launcher/service.py` | Add `_validate_browse_path(value: str) -> Path` and `async def browse(db, path: str \| None) -> schemas.DirectoryListing`. Reuses the existing `_projects_root(db)` helper for the default. | Layering rule: routes parse, service holds the logic. `_validate_browse_path` mirrors `settings.service._validate_projects_root` (expanduser → absolute check → is-dir check) so both raise the same shape of 400. |
| `backend/app/api/v1/launcher/routes.py` | Add `@router.get("/launcher/browse", response_model=schemas.DirectoryListing, operation_id="browseDirectories")` taking `path: str \| None = Query(None, description=...)` and `db: AsyncSession = Depends(get_db)`, delegating to `service.browse`. | Same router, same `tags=["launcher"]`, so the TS method lands on the existing `LauncherApi` the frontend facade already exposes as `api.launcher`. |

Service logic, concretely:

```python
async def browse(db: AsyncSession, path: str | None) -> schemas.DirectoryListing:
    target = _validate_browse_path(path) if path is not None else await _projects_root(db)
    ...
```

- `_validate_browse_path`: `expanded = Path(value).expanduser()`; raise
  `InvalidBrowsePathError` if not `expanded.is_absolute()`; `resolved =
  expanded.resolve()`; raise if not `resolved.is_dir()`. The nonexistent and
  not-a-directory cases both fall out of the single `is_dir()` check but get
  distinct messages via an `exists()` probe, matching the two separate messages
  `_validate_projects_root` produces.
- The default branch does **not** re-validate: `read_settings` already
  guarantees an absolute, expanded path, and `list_projects` already tolerates a
  root that has since vanished. If the stored root is no longer a directory,
  `browse` returns an empty `entries` list with that path — same posture as
  `list_projects` returning `[]` — rather than 400ing on a request the user did
  not parameterize.
- Listing: iterate `sorted(target.iterdir(), key=lambda p: p.name)`, keep
  `child.is_dir() and not child.name.startswith(".")`, wrapping the per-entry
  `is_dir()` in `try/except PermissionError: continue` so one unreadable entry
  cannot fail the whole listing. A `PermissionError` from `iterdir()` itself —
  the whole directory is unreadable — raises `InvalidBrowsePathError` (400),
  because there is nothing to show and silently returning an empty list would
  read as "this folder is empty".
- `parent`: `None` when `target.parent == target` (filesystem root), else
  `str(target.parent)`.
- Filesystem I/O stays synchronous, matching `list_projects` directly above it.

### Frontend

| Path | Change | Why |
|---|---|---|
| `frontend/src/features/sessions/queries.ts` | Add `folderBrowserOpenAtom` (`atom(false)`), `browsePathAtom` (`atom<string \| null>(null)` — `null` means "let the server default to `projects_root`"), and `browseDirectoriesQueryAtom` = `atomWithQuery` with `queryKey: ['browseDirectories', get(browsePathAtom)]`, `queryFn: () => api.launcher.browseDirectories(get(browsePathAtom) ?? undefined).then(r => r.data)`, and `enabled: get(folderBrowserOpenAtom)`. | The feature's atoms live here (house layout + the file's own precedent). `enabled` keeps the closed dialog from making a browse call, which matters because the existing `launchSessionDialog.ct.tsx` mock throws on any unexpected request. The path is part of the query key so each visited directory is cached and a re-visit is instant. |
| `frontend/src/features/sessions/components/FolderPicker.tsx` (new) | The panel: breadcrumb, up-one-level, subdirectory list, **Use this folder**, plus `Skeleton` and inline-error-with-Retry states copied from the dialog's existing two blocks. Props: `onPicked(path: string) => void` so the dialog owns what happens after a save. | Keeps `LaunchSessionDialog.tsx` from growing a second concern; the dialog file is already ~275 lines. Feature-local component folder, matching every sibling. |
| `frontend/src/features/sessions/components/LaunchSessionDialog.tsx` | Add a `Browse` button beside `Save` in the projects-root row (`type="button"`, like the sibling buttons); render `<FolderPicker />` under that row when `folderBrowserOpenAtom` is true; on `onPicked`, close the panel and let the existing `useEffect([open, appSettings])` re-seed `rootDraft` from the refetched settings. Reset both browser atoms in `reset()` so a reopened dialog starts from `projects_root` again. | The request puts the affordance beside the projects-root control. Reusing `updateSettingsMutationAtom` means the save path, its error toast, and the `launcherProjectsQueryAtom` invalidation are all already correct. |

Panel behavior detail:

- Breadcrumb is derived from the response's `path` (not from local state), split
  on `/`: each segment is a `<button type="button">` that sets `browsePathAtom`
  to the joined prefix; the leading `/` is its own clickable root crumb. Driving
  it from the server's resolved path means a symlinked or `..`-containing path
  displays canonically.
- Up-one-level is a button labelled `Up one level` (with a `lucide-react`
  `ChevronUp`/`CornerLeftUp` icon, consistent with the dialog's `AlertTriangle`
  usage), disabled when `parent` is `null`.
- Each subdirectory row is a `<button type="button">` — inside a `<form>`,
  omitting `type` would submit the launch form (the exact bug fixed in commit
  `430182e`).
- **Use this folder** calls `saveSettings({ projects_root: data.path })`, shows
  the existing `toast.success('Projects root updated')` / error toast pair, then
  `onPicked(data.path)`. No extra refetch call is added: the mutation's
  `onSuccess` already invalidates `APP_SETTINGS_QUERY_KEY` **and**
  `LAUNCHER_PROJECTS_QUERY_KEY`.

### Generated client + schema (hand-extended)

| Path | Change |
|---|---|
| `frontend/openapi.json` | Add `"/api/v1/launcher/browse"` with the `get` operation (tag `launcher`, `operationId: browseDirectories`, one optional `path` query param with an `anyOf [string, null]` schema, 200 → `#/components/schemas/DirectoryListing`, 422 → `HTTPValidationError`), inserted next to the other `/api/v1/launcher/*` paths (~line 1607). Add `DirectoryEntry` and `DirectoryListing` to `components.schemas` in their alphabetical position. |
| `frontend/src/api/generated/api.ts` | Add `export interface DirectoryEntry` and `export interface DirectoryListing` in the alphabetical model block, and `browseDirectories` in **all four** `LauncherApi` blocks — `LauncherApiAxiosParamCreator` (~6438), `LauncherApiFp` (~6653), `LauncherApiFactory` (~6740), `class LauncherApi` (~6811) — first in each block, which is where the generator's alphabetical ordering puts it. The param creator follows the `listCodingAssetUsage` pattern (`if (path !== undefined) localVarQueryParameter['path'] = path;` before `setSearchParams`). |
| `frontend/src/api/generated/index.ts` | No change — it is `export * from "./api"`. |
| `frontend/src/api/client.ts` | No change — `browseDirectories` lands on the already-registered `LauncherApi`. |

Hand-extension is what v1.26 did (commit `09b2e3c`: "hand-regenerated
typescript-axios client (no shell was available to run the generator)"). The
edits must be byte-shaped like generator output so the next real
`pnpm generate:api:local` produces no diff.

### Docs

| Path | Change |
|---|---|
| `docs/API_CONTRACT.md` | Append `# API Contract v1.27 — server-side folder picker`, after the v1.26 section (file currently ends at line 2455). Sections: **New schemas** (`DirectoryEntry`, `DirectoryListing`), **New endpoint** (the table row for `GET /api/v1/launcher/browse`), **Behavior** — that browsing is deliberately unrestricted and why, that hidden entries and files are excluded, that per-entry `PermissionError` is skipped while an unreadable target 400s, that `parent` is `null` only at the filesystem root, that the default is the stored `projects_root`, and that nothing is written by this endpoint. |

---

## Data / contract impact

- **No database change.** No new table, no new column, no Alembic revision. The
  head stays `0022_session_launch_run_id`.
- **No settings change.** The picker writes through the existing
  `PATCH /api/v1/settings`; `AppSettings` / `AppSettingsUpdateRequest` are
  untouched.
- **OpenAPI**: two new schemas (`DirectoryEntry`, `DirectoryListing`), one new
  path, one new `operation_id`. Purely additive — no existing operation, schema,
  or field changes shape, so the existing generated client keeps compiling and
  no frontend caller needs a signature update.
- **New failure mode on the wire**: `browseDirectories` can 400 with a
  `{"detail": "..."}` body. `apiErrorMessage` (`frontend/src/api/client.ts`)
  already unwraps exactly that shape.
- **Nothing is written server-side by the browse endpoint.** It is read-only;
  the only write in the whole flow is the existing settings PATCH.

---

## Test strategy

Existing frameworks only — pytest for the backend, Playwright CT for the
frontend. No new test dependency, no new config.

**Backend unit — `backend/tests/unit/test_launcher_browse.py` (new)**, in the
style of `test_launcher_names.py` (no DB, no FastAPI):

- `_validate_browse_path` accepts an absolute existing `tmp_path` and returns it
  resolved.
- Parametrized rejections, each asserting `InvalidBrowsePathError`: a relative
  path (`"relative/path"`), a nonexistent absolute path
  (`tmp_path / "nope"`), and a path that is a file, not a directory
  (`tmp_path / "f.txt"` after `write_text`).
- `~` expansion: `"~"` is accepted (expands to an absolute existing dir).

**Backend integration — `backend/tests/integration/test_launcher.py` (extend)**,
using the existing `client` fixture and `tmp_path`, with `PATCH
/api/v1/settings` used to point `projects_root` at a `tmp_path` fixture tree
(`alpha/`, `beta/`, `.hidden/`, `file.txt`, and `alpha/nested/`):

- Happy path: `GET /api/v1/launcher/browse?path=<tmp>` → 200, `path` equals the
  resolved tmp dir, `parent` is its parent, `entries` is exactly
  `[{alpha}, {beta}]` in name order.
- Default: `GET /api/v1/launcher/browse` with no param returns the same listing
  as passing the stored `projects_root` explicitly.
- Hidden exclusion and files-never-returned: `.hidden` and `file.txt` are absent
  from `entries` (asserted by name set, so one assertion covers both).
- Descend: `?path=<tmp>/alpha` lists `nested` and reports `parent == <tmp>`.
- Filesystem root: `?path=/` returns `parent: null`.
- Rejections, each asserting 400: relative path, nonexistent path, a path that
  is a regular file.
- Permission skip: `chmod 0o000` on a subdirectory of the browsed dir, assert
  200 and that the listing still contains the readable siblings.
  `pytest.mark.skipif(os.geteuid() == 0)` because root ignores the mode bits,
  and the mode is restored in a `finally` so the tmp tree can be cleaned up.

**Frontend CT — `frontend/tests/components/folderPicker.ct.tsx` (new)**,
modelled on `launchSessionDialog.ct.tsx` (same `CORS` headers, same `json()`
helper, same `page.route('**/api/v1/**')` router that throws on an unexpected
request). A small in-memory tree keyed by path serves `browse` responses, and
the mock records every `PATCH /settings` body:

- Navigate-and-select: open the dialog → click **Browse** → assert the
  breadcrumb shows the projects root and the two child rows are listed → click a
  child row → assert the breadcrumb and rows updated to the child and that the
  browse request carried `path=<child>` → click **Use this folder** → assert one
  `PATCH /api/v1/settings` with `{projects_root: <child>}`, that the panel
  closed, and that the projects-root input now shows the new path.
- Up-one-level returns to the parent, and is disabled when the response's
  `parent` is `null`.
- Error state: a browse route that 500s renders the inline error with a
  **Retry** button, and clicking Retry re-issues the request (asserted by call
  count).

Distinct directory paths per test keep the module-level `QueryClient` in
`TestProviders` from serving one test's cached listing to another.

**Regression gates (must stay green, unchanged):** `backend/tests` (including
`tests/integration/test_launcher.py`'s existing launch/interview walks and
`test_settings.py`), `factory/tests` (untouched by this change), and
`pnpm typecheck` in `frontend/`. `frontend/tests/components/launchSessionDialog.ct.tsx`
must keep passing **unmodified** — its mock throws on unexpected requests, which
is the live check that the closed picker issues no browse call.

---

## Risks

1. **The existing `launchSessionDialog.ct.tsx` mock throws on unexpected
   requests.** If `browseDirectoriesQueryAtom` fetches while the picker is
   closed, four existing tests fail. Mitigated by the `enabled:
   get(folderBrowserOpenAtom)` gate — and that failure is loud, not silent.
2. **Un-generated client drift.** The hand-written `browseDirectories` must
   match what `openapi-generator` would emit; a mismatch is invisible until
   someone regenerates and gets a surprise diff. Mitigated by copying the
   `listCodingAssetUsage` (optional query param) and `listLauncherProjects`
   (no-arg GET) shapes literally, and by keeping `frontend/openapi.json` the
   source of truth for the next regeneration.
3. **`openapi.json` is large and hand-edited.** A malformed insert breaks
   `generate:api:local` for everyone. Mitigated by inserting whole,
   well-formed objects next to the sibling launcher paths and keeping the
   2-space/4-space indentation the file already uses.
4. **Browsing is unrestricted by design.** `GET /api/v1/launcher/browse?path=/`
   enumerates directory *names* anywhere the backend user can read. Accepted
   per the request (local single-user app; `PATCH /api/v1/settings` already
   accepts any absolute path) and stated explicitly in the v1.27 doc section.
   No file contents are ever returned.
5. **Synchronous filesystem I/O in an async route.** A directory with tens of
   thousands of entries, or one on a stalled network mount, blocks the event
   loop. Accepted to match `list_projects` two functions above it; noted rather
   than fixed, since fixing it here would leave two different I/O idioms in one
   file.
6. **Permission handling has two layers** — per-entry skip vs. whole-directory
   400 — and getting them backwards means either a spurious 400 or a listing
   that lies about being empty. Both are covered by tests.
7. **Symlink loops / very deep trees.** `.resolve()` on a symlink cycle raises
   `OSError` (`ELOOP`), which is not a `PermissionError` and would 500. Mitigate
   by catching `OSError` in `_validate_browse_path` and re-raising as
   `InvalidBrowsePathError`.
8. **Windows path handling.** The breadcrumb splits on `/`. This is a macOS/
   POSIX-only tool (`~/.masterwork`, `python3`, `git init`), so this is
   consistent with the rest of the codebase but would need work if that ever
   changed.
9. **Shared `QueryClient` across CT tests in one file** can serve a stale browse
   listing to a later test. Mitigated by using distinct paths per test.
10. **`reset()` must clear the browser atoms**, or reopening the dialog shows
    the last-browsed directory instead of the (possibly just-changed) projects
    root. Covered by the navigate-and-select test's final assertions.

---

## Assumptions

Recorded because this run is unattended; each is the reading a careful
colleague would take, and each is cheap to reverse.

1. **Nested panel, not a Popover.** `@radix-ui/react-popover` is not in
   `frontend/package.json` and this run has no shell to install it; the request
   explicitly allowed "a shadcn Popover **or nested panel**".
2. **New `InvalidBrowsePathError` rather than reusing `InvalidSettingError`** —
   same 400 pattern, distinct name, since a browse path is not a setting.
3. **A wholly unreadable target directory 400s**; only *entries* that raise
   `PermissionError` are silently skipped. The request specified the per-entry
   rule and was silent on the target itself.
4. **`~` is expanded before the absolute check**, matching
   `_validate_projects_root`.
5. **Symlinked subdirectories are listed** (`Path.is_dir()` follows symlinks),
   matching `list_projects`.
6. **The client and `openapi.json` are hand-extended**, as v1.26 did, because no
   shell is available to run `openapi-generator-cli`.
7. **The contract section is numbered v1.27**, the next number after v1.26.
8. **No second refetch is wired for `launcherProjectsQueryAtom`** — the existing
   `updateSettingsMutationAtom.onSuccess` already invalidates that key, so the
   request's "then refetches launcherProjectsQueryAtom" is satisfied by the code
   that is already there.
9. **The browse endpoint does not offer the `is_git_repo` flag** that
   `LauncherProject` carries; it is a folder picker for the *root*, not a
   project picker.
