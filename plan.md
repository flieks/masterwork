# Plan — semantic skill search on the skills catalog page

## The change in one paragraph

skills.sh already ranks `/api/search` results by relevance and says how it ranked
them in a top-level `searchType`, and masterwork throws that away by re-sorting the
merged list on install count. This change parses `searchType`, carries it through
`skill_catalog.CatalogResult` onto a new `CatalogSearchResponse.search_type` field,
and keeps skills.sh's own order whenever the search was semantic (GitHub-only extras
are appended after the skills.sh hits, installs-desc among themselves); fuzzy and
unknown keep today's installs-desc sort. The GitHub repo-search leg is skipped
entirely for queries longer than three words, silently — a descriptive sentence only
yields noise there, and a deliberate skip is not a source failure. Because skills.sh
search records carry no description, the top ten skills.sh hits are enriched
concurrently from their skills.sh detail page: one 3 s GET each, no retry, reading
`description` out of the page's `application/ld+json` SoftwareApplication block, with
any failure degrading that one card to `""`. On top of that ranking work, the
installed tab gets a describe-to-find path: `POST /api/v1/skills/installed/match`
puts every installed skill's name and frontmatter description into one prompt for the
cheap `ClaudeRunner`, parses a strict JSON array of `{name, reason}` defensively
(unknown names dropped, capped at 8), and 502s when the runner fails or answers with
something unparseable. The catalog UI gains a describe-what-you-need placeholder and
a "Ranked by meaning" / "Ranked by installs" caption; the installed skills tab gains a
"Find by description" button that opens an inline textarea panel and renders the
matched names as the existing asset cards with the model's reason as a caption. No
new dependencies, no embeddings, SQLite untouched.

## Files to add or change

### Backend — catalog ranking and descriptions (parts 1 and 2)

- **`backend/app/services/skill_catalog.py`** (change). The whole of parts 1 and 2
  land in this client-free leaf.
  - Add `SearchType = Literal["semantic", "fuzzy", "unknown"]` and a
    `search_type: SearchType` field on `CatalogResult`.
  - Add a frozen `_SkillsShHits` dataclass (`skills: list[CatalogSkill]`,
    `search_type: SearchType`) and make `_search_skills_sh` return it. Read
    `searchType` only when the decoded body is a dict; accept exactly `"semantic"`
    and `"fuzzy"`, anything else (absent, misspelt, non-string, bare-list body) is
    `"unknown"`. `_skills_sh_records` is unchanged.
  - `_collect` currently maps a leg's outcome to `(skills, errors)`. Split it so the
    skills.sh leg also yields its search type, defaulting to `"unknown"` when that
    leg raised.
  - `search_catalog(query, limit, *, transport)` gains the skip and the new order:
    - `_word_count(query) > 3` (i.e. `len(query.split()) > 3`) means the GitHub leg
      is never scheduled. No `SourceError` is produced for it and nothing is logged
      as a failure.
    - The "both sources failed" guard becomes "every *attempted* source failed":
      raise `SkillCatalogError` when `len(errors) == attempted`, where `attempted`
      is 2 normally and 1 when GitHub was skipped. Without this, a >3-word query
      whose skills.sh leg dies would 200 with an empty list.
    - `_merge` is reshaped to preserve provenance order instead of returning one
      dict's values: it returns `(skills_sh_ordered, github_only)` — skills.sh hits
      in the order skills.sh returned them (still carrying over a license resolved
      by a colliding GitHub record), then the GitHub records that survived dedupe.
    - `search_type == "semantic"`: final order is `skills_sh_ordered` verbatim, then
      `sorted(github_only, key=(-(installs or 0), name.casefold()))`. Otherwise:
      today's single installs-desc sort over both lists together, unchanged.
    - Truncate to `limit`, then enrich, then return
      `CatalogResult(skills=..., errors=..., search_type=...)`.
  - Add `SKILLS_SH_PAGE_BASE = "https://www.skills.sh"`, `_DESCRIPTION_TIMEOUT = 3.0`
    and `_MAX_DESCRIPTION_FETCHES = 10`.
  - Add `async def _enrich_descriptions(client, skills) -> list[CatalogSkill]`:
    picks the first 10 entries with `registry == "skills_sh"` and an empty
    `description`, fires one `client.get(f"{SKILLS_SH_PAGE_BASE}/{owner}/{repo}/{skill}",
    timeout=_DESCRIPTION_TIMEOUT)` each through `asyncio.gather(...,
    return_exceptions=True)` — deliberately **not** `_request_with_retry`, because
    the spec is one attempt and no retry — and `dataclasses.replace`s the description
    in. Any exception, any status >= 300, any unparseable page leaves that hit at
    `""`. The enrichment runs inside the existing `async with httpx.AsyncClient(...)`
    block, so the merge/order/limit steps move inside that block too.
  - Add a module-level `_LD_JSON_RE` and `def _description_from_page(html: str) -> str`:
    finds every `<script type="application/ld+json">…</script>` body,
    `json.loads` each (skipping the ones that fail), flattens a list body and an
    `@graph`, takes the first entry whose `@type` is `SoftwareApplication`
    (case-insensitive, and tolerating a list-valued `@type`) with a non-empty string
    `description`, and returns it `html.unescape`d and stripped. Returns `""` when
    nothing matches. Pure function, so it is unit-testable without a transport.

- **`backend/app/api/v1/skills/schemas.py`** (change). `CatalogSearchResponse` gains
  `search_type: Literal["semantic", "fuzzy", "unknown"] = Field(..., description=...)`,
  spelled as the request asks rather than as a `StrEnum` like `SkillRegistry`; the
  generator still emits a TS string union for it.

- **`backend/app/api/v1/skills/service.py`** (change). `search_catalog` passes
  `search_type=result.search_type` into the response. No other change — the
  `installed` flag and error mapping stay as they are.

### Backend — describe-to-find over installed skills (part 4)

- **`backend/app/services/skill_match_parser.py`** (new). Mirrors
  `app/services/links_parser.py`: `@dataclass(frozen=True) class ParsedMatch(name,
  reason)` and `def extract_matches(text: str) -> list[ParsedMatch] | None`. Parses a
  bare JSON array first, and falls back to the last fenced block (```json or
  ```matches) when the model wraps it. Returns `None` for a non-array, non-JSON, or
  entries without a usable string `name`; a missing/odd `reason` degrades to `""`
  rather than failing the whole reply.

- **`backend/app/api/v1/skills/match_service.py`** (new). Mirrors
  `app/api/v1/projects/links_service.py`.
  - `_installed_skill_lines(providers)` uses the existing scanner —
    `app.api.v1.assets.service.list_assets(providers, kind=AssetKind.skill)` — takes
    `name` + `description`, dedupes by name (a generic skill is scanned once per
    agent root it is linked into), truncates each description to 200 chars, and runs
    every line through `app.services.redact.redact`, exactly as `links_service` does.
  - `build_match_prompt(query, lines)` states the user's description, lists the
    candidate `name — description` lines, and demands ONLY a JSON array of
    `{"name": ..., "reason": ...}`, at most 8, best first, names chosen strictly from
    the list.
  - `async def match_installed(providers, runner, query) -> SkillMatchResponse`:
    `runner.run_once(prompt)`; `ClaudeRunnerError` → `SkillMatchError`;
    `extract_matches(...) is None` → `SkillMatchError`; then drop names not in the
    scanned set, dedupe, cap at 8, and return. An empty surviving list returns
    `matches: []` with 200 — "nothing matched" is a real answer here, unlike
    `links_service`, whose empty case means the model ignored the catalog.

- **`backend/app/core/exceptions.py`** (change). Add `SkillMatchError(DomainError)`
  with `status_code = 502`, docstring in the same register as
  `LinkSuggestionError`.

- **`backend/app/api/v1/skills/schemas.py`** (change, same file as above). Add
  `SkillMatchRequest(query: str = Field(..., min_length=1))`,
  `SkillMatch(name: str, reason: str)` and
  `SkillMatchResponse(matches: list[SkillMatch])`.

- **`backend/app/api/v1/skills/routes.py`** (change). Add

  ```
  POST /skills/installed/match  →  operation_id="matchInstalledSkills"
       response_model=schemas.SkillMatchResponse
       body: SkillMatchRequest
       deps: providers = Depends(get_providers), runner = Depends(get_light_runner)
  ```

  Declared **before** the `/skills/installed/{name}/…` routes for readability; there
  is no actual path collision, since those all carry a suffix segment. `get_light_runner`
  is the cheapest runner configured in `app/api/deps.py` (`settings.claude_light_model`).
  No new dependency function is added to `deps.py`.

### Frontend

- **`frontend/src/features/assets/components/SkillCatalog.tsx`** (change).
  Placeholder becomes `Describe what you need, or type a skill name…`. Above the
  results list (only when there are results) render a muted caption:
  `Ranked by meaning` when `data.search_type === 'semantic'`, otherwise
  `Ranked by installs`. Same `text-xs text-muted-foreground` register as the existing
  source-error line.

- **`frontend/src/features/assets/queries.ts`** (change). Add

  ```ts
  export const matchInstalledSkillsMutationAtom = atomWithMutation<SkillMatchResponse, string>(...)
  ```

  calling `api.skills.matchInstalledSkills({ query })`. A mutation, not a query: the
  request fires only on the Find click, never on a keystroke.

- **`frontend/src/features/assets/components/SkillMatchPanel.tsx`** (new). The inline
  panel: a `Textarea` (`aria-label="Describe what you need"`), a `Find` button
  (disabled while empty or pending), a pending state, and an error line via
  `apiErrorMessage`. It owns no list rendering — it reports the matches upward.

- **`frontend/src/features/assets/components/AssetListPage.tsx`** (change). For
  `kind === 'skill'` on the installed tab only: a `Find by description` button beside
  the search input toggles `SkillMatchPanel`. While matches are showing, the list
  renders the matched assets instead of the search results, in whichever view the
  `ViewToggle` is on, with a `Clear` affordance that drops back to the normal list.
  The match source is the *unfiltered* asset list — the page subscribes to
  `assetsQueryAtom(assetsListKey(kind, ''))` unconditionally alongside the existing
  debounced-query atom, which is the same cache entry (no extra request) whenever the
  search box is empty. Matched assets are ordered by the model's ranking, not by the
  scanner's order.

- **`frontend/src/features/assets/components/AssetGrid.tsx`** and
  **`AssetTable.tsx`** (change). Both take an optional `captions?: Map<string, string>`
  keyed by asset name. The grid renders the caption as a muted line above the
  description; the table renders it under the name cell. Absent prop = today's
  rendering exactly.

## Data and contract impact

- **No database change.** No model, no migration, no repository touched. SQLite stays
  as it is, and nothing about the match endpoint is persisted.
- **OpenAPI**: `CatalogSearchResponse` gains a required `search_type`; three new
  schemas (`SkillMatchRequest`, `SkillMatch`, `SkillMatchResponse`) and one new
  operation `matchInstalledSkills` appear. A required field added to a response is
  backward-compatible for readers and forward-breaking for any hand-built fixture,
  which is why the fixtures below must be updated in the same commit.
- **Generated client and contract doc are the build stage's job.** The build stage
  runs `make api` (rewrites `frontend/openapi.json` and
  `frontend/src/api/generated/*`) and adds the two endpoint lines plus a short
  ranking note to `docs/API_CONTRACT.md` near the existing skills block (lines ~3155
  and ~3440). **The plan stage touches neither.** Expect typescript-axios to name the
  inline literal union `CatalogSearchResponseSearchTypeEnum`; comparing the field to
  `'semantic'` still typechecks, so no component code depends on that name.
- **Outbound calls**: one new third-party GET pattern,
  `https://www.skills.sh/{owner}/{repo}/{skill}`, at most 10 per search, 3 s each,
  through the same injected transport as every other call in the leaf — tests keep
  using `httpx.MockTransport` and no test reaches the network.

## Test strategy

Backend, `pytest`, existing layout:

- **`backend/tests/unit/test_skill_catalog.py`** (change; keeps its `_route` host
  router, extended with a `skills_sh_page` handler for `www.skills.sh` paths other
  than `/api/search`):
  - a `searchType: "semantic"` body keeps skills.sh's own order even when a later hit
    has more installs, and appends a GitHub-only extra after every skills.sh hit;
  - `searchType: "fuzzy"`, a missing `searchType`, and a bare-list body all keep
    today's installs-desc order and report `search_type` as `"fuzzy"`/`"unknown"`;
  - a 4-word query never hits `api.github.com` (the router raises on an unexpected
    host) and returns `errors == []`;
  - a 3-word query still queries GitHub;
  - a 4-word query whose skills.sh leg fails raises `SkillCatalogError` rather than
    returning an empty 200;
  - description enrichment: the detail page's ld+json fills the description; at most
    10 pages are fetched for 12 hits; a 500, a timeout, a page with no ld+json, and a
    page whose ld+json is malformed each leave that one hit at `""` while its
    neighbours still get theirs.
- **`backend/tests/unit/test_skill_match_parser.py`** (new): bare array, fenced
  array, array-of-junk, non-JSON prose, missing `reason`, non-list JSON.
- **`backend/tests/integration/test_skills.py`** (change): the catalog search
  response carries `search_type`.
- **`backend/tests/integration/test_skill_match.py`** (new), using
  `tests.helpers.FakeRunner` and `providers_for(claude_tree)` exactly as
  `tests/integration/test_diagrams.py` does, with
  `app.dependency_overrides[get_light_runner]`:
  - a scripted JSON array returns the matches in the model's order;
  - a name that is not installed is dropped;
  - more than 8 returned entries are capped at 8;
  - `FakeRunner(error=...)` → 502;
  - a non-JSON reply → 502;
  - an empty array → 200 with `matches: []`;
  - the prompt the fake recorded contains every installed skill name.

Frontend:

- **`frontend/tests/components/harness/catalogFixtures.ts`** (change):
  `catalogSearchResponse` gains `search_type: 'semantic'` by default; add a
  `skillMatchResponse()` fixture for the new endpoint.
- **`frontend/tests/components/skillCatalog.ct.tsx`** (change): assert the new
  placeholder, that a semantic response renders `Ranked by meaning`, and that a
  `search_type: 'fuzzy'` response renders `Ranked by installs`.
- **`frontend/tests/components/skillMatch.ct.tsx`** (new): on the installed tab,
  `Find by description` opens the panel; typing alone fires no request; clicking
  `Find` posts exactly once to `/api/v1/skills/installed/match`; the returned names
  render as cards with their reasons; `Clear` restores the full list.
- `npm run typecheck` (or the repo's equivalent script) must be green — the new
  required `search_type` field breaks any fixture that omits it, which is the point.

## Risks

- **skills.sh has no published contract.** `searchType` is asserted by the request,
  not by a schema, and the detail page's ld+json block is scraped HTML. Both are
  parsed defensively and both degrade to today's behaviour (`"unknown"` ordering,
  empty description) rather than failing a search, but a site redesign silently
  removes the feature. Worth a follow-up check if descriptions ever go blank en masse.
- **Ten extra HTTP requests per search.** They are concurrent with a 3 s cap and no
  retry, so the worst case adds ~3 s to a search that would otherwise be ~1 s. If
  that proves too slow in practice, the fix is to lower the count, not the timeout.
- **The >3-word GitHub skip is invisible by design.** A user typing a four-word
  proper-noun query gets no GitHub results and no explanation. That is what the
  request asks for; the caption ("Ranked by meaning") is the only hint.
- **The match endpoint spends a real model call per click.** It is click-triggered,
  never debounced, and there is no caching — deliberate, and the reason the button
  is separate from the search box.
- **Prompt size grows with the installed catalog.** ~100 skills at a 200-char
  description ceiling is a small prompt; a machine with many hundreds would be worth
  paging, but that is out of scope here.
- **`_merge`'s reshape is the one behavioural risk in existing code.** Its dedupe
  semantics (skills.sh wins, GitHub's resolved license carries over) must be preserved
  exactly; the existing
  `test_dedupes_on_owner_repo_skill_skills_sh_wins_and_carries_license` is the guard
  and must keep passing unchanged.
