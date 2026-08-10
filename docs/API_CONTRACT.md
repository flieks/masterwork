# API Contract v1 — FROZEN

Both sides implement exactly this. Backend: FastAPI with **explicit
`operation_id`s and schema names as listed** (the frontend's typescript-axios
client is generated from them; drift breaks the build). Base path `/api/v1`.
All timestamps are ISO 8601 UTC strings. IDs of chat entities are UUIDv4
strings. Asset ids are slugs `"{provider}:{kind}:{name}"` (URL-encoded when
used in a path).

## Schemas

```
AssetSummary {
  id: string            // "claude:skill:frontend-dev"
  kind: "skill" | "agent"
  provider: string      // "claude"
  name: string          // "frontend-dev"
  title: string         // frontmatter `name`/`title` or name fallback
  description: string   // frontmatter `description`, "" if none
  path: string          // absolute file path
  updated_at: string    // file mtime
}
AssetDetail = AssetSummary + { content: string }   // full markdown incl. frontmatter
AssetUpdateRequest { content: string }

ChatSession {
  id: string
  title: string
  created_at: string
  updated_at: string
}
ChatSessionCreateRequest { title?: string | null }   // default "New chat", retitled from first message
ChatSessionUpdateRequest { title: string }

ProposalChange {
  path: string                          // absolute
  action: "update" | "create" | "delete"
  new_content: string | null            // full new file content; null for delete
  description: string
  asset_id: string | null               // set when path maps to a known asset
}
Proposal {
  id: string
  status: "pending" | "applied" | "rejected" | "failed"
  summary: string
  changes: ProposalChange[]
  error: string | null                  // set when status == "failed"
  created_at: string
}

ChatMessage {
  id: string
  session_id: string
  role: "user" | "assistant" | "error"
  content: string                       // markdown; proposal block already stripped
  proposal: Proposal | null             // only on assistant messages that propose changes
  created_at: string
}
ChatMessageCreateRequest { content: string }
ChatExchange { user_message: ChatMessage, assistant_message: ChatMessage }
```

## Endpoints

| Method & path | operation_id | Request | Response |
|---|---|---|---|
| GET `/api/v1/assets?kind=&q=` | `listAssets` | query: `kind` optional (`skill`\|`agent`), `q` optional free text | `AssetSummary[]` |
| GET `/api/v1/assets/{asset_id}` | `getAsset` | — | `AssetDetail` (404 if unknown) |
| PUT `/api/v1/assets/{asset_id}` | `updateAsset` | `AssetUpdateRequest` | `AssetDetail` |
| GET `/api/v1/chat/sessions` | `listChatSessions` | — | `ChatSession[]` (updated_at desc) |
| POST `/api/v1/chat/sessions` | `createChatSession` | `ChatSessionCreateRequest` | `ChatSession` (201) |
| PATCH `/api/v1/chat/sessions/{session_id}` | `updateChatSession` | `ChatSessionUpdateRequest` | `ChatSession` |
| DELETE `/api/v1/chat/sessions/{session_id}` | `deleteChatSession` | — | 204 (cascades messages+proposals) |
| GET `/api/v1/chat/sessions/{session_id}/messages` | `listChatMessages` | — | `ChatMessage[]` (created_at asc) |
| POST `/api/v1/chat/sessions/{session_id}/messages` | `createChatMessage` | `ChatMessageCreateRequest` | `ChatExchange` (synchronous; may take up to 300 s) |
| POST `/api/v1/proposals/{proposal_id}/accept` | `acceptProposal` | — | `Proposal` (status `applied`, or `failed` + `error`; `failed` proposals may be retried) |
| POST `/api/v1/proposals/{proposal_id}/reject` | `rejectProposal` | — | `Proposal` (status `rejected`) |

Search semantics for `q`: case-insensitive substring match against name, title,
description, or full file content.

Errors: FastAPI default `{ "detail": string }` with proper status codes
(404 unknown asset/session/proposal, 409 accepting or rejecting an
`applied`/`rejected` proposal — `pending` and `failed` are actionable,
400 invalid asset id, 502 when the claude CLI fails).

## Claude runner (backend internals, for reference)

- Invocation: `claude -p <prompt> --model $CLAUDE_MODEL --output-format json
  --allowedTools Read Glob Grep --disallowedTools Bash Edit MultiEdit Write
  NotebookEdit Task --strict-mcp-config`, cwd `~/.claude`, first call also
  `--append-system-prompt` (app context + proposal-block instructions);
  later calls add `--resume <claude_session_id>` (stored on the session row).
  The deny list is what actually enforces read-only: user-level settings (e.g.
  `permissions.defaultMode: "auto"`) can auto-approve edit tools, and
  `--allowedTools` only ever adds approvals.
- Parse stdout JSON: `result` (reply text) and `session_id`.
- A trailing fenced block ` ```proposal ` containing
  `{"summary": str, "changes": [{path, action, new_content, description}]}`
  becomes a Proposal row and is stripped from the stored message content.
  An update/create change with null `new_content` can never apply, so such a
  proposal is created directly as `failed` with an explanatory `error`.
- Accept applies changes in the backend with path validation: each resolved
  path must live under a provider root (`~/.claude/skills`, `~/.claude/agents`).

## Config (backend/.env)

```
DATABASE_URL=postgresql+asyncpg://localhost:5432/masterwork
CORS_ORIGINS=http://localhost:5192
CLAUDE_BIN=claude
CLAUDE_MODEL=opus
CLAUDE_TIMEOUT_SECONDS=300
```

---

# API Contract v1.1 — Projects & Diagrams (FROZEN additions)

Additive on top of v1. Same rules: explicit `operation_id`s and schema names.

## Changed v1 schemas

```
ChatSession += { project_id: string | null }          // null = global chat
ChatSessionCreateRequest += { project_id?: string | null }
Proposal += { project_update: ProjectUpdate | null }  // a proposal now carries
                                                      // file changes, a project
                                                      // update, or both
```

`listChatSessions` gains an optional `project_id` query param:
omitted → all sessions; literal string `"none"` → global sessions only
(project_id IS NULL); a UUID → that project's sessions (404 if unknown project).

## New schemas

```
Project {
  id: string                    // uuid
  name: string
  goal: string                  // scenario description, markdown, "" default
  flow_mermaid: string | null   // mermaid source: how the assets work together
  asset_ids: string[]           // linked assets, e.g. "claude:skill:azure-deploy"
  created_at: string
  updated_at: string
}
ProjectCreateRequest { name: string, goal?: string }
ProjectUpdateRequest { name?: string, goal?: string, flow_mermaid?: string | null, asset_ids?: string[] }  // partial

ProjectUpdate {                 // proposal payload proposed by the chatbot
  project_id: string
  name: string | null           // null = leave unchanged (same for all below)
  goal: string | null
  flow_mermaid: string | null
  asset_ids: string[] | null
  description: string           // human-readable summary of the update
}

AssetDiagram {
  asset_id: string
  mermaid: string
  generated_at: string
  stale: boolean                // true when file changed since generation
}
```

## New endpoints

| Method & path | operation_id | Request | Response |
|---|---|---|---|
| GET `/api/v1/projects` | `listProjects` | — | `Project[]` (updated_at desc) |
| POST `/api/v1/projects` | `createProject` | `ProjectCreateRequest` | `Project` (201) |
| GET `/api/v1/projects/{project_id}` | `getProject` | — | `Project` |
| PATCH `/api/v1/projects/{project_id}` | `updateProject` | `ProjectUpdateRequest` | `Project` |
| DELETE `/api/v1/projects/{project_id}` | `deleteProject` | — | 204 (cascades the project's chat sessions) |
| GET `/api/v1/assets/{asset_id}/diagram` | `getAssetDiagram` | — | `AssetDiagram` (404 if never generated) |
| POST `/api/v1/assets/{asset_id}/diagram` | `generateAssetDiagram` | — | `AssetDiagram` (synchronous claude -p, up to 300 s) |

## Behavior

- **Project-scoped chat**: a session with `project_id` gets an extended system
  prompt: project name, goal, current `asset_ids` (with their descriptions),
  current flow diagram, plus instructions that the assistant may ALSO emit a
  fenced block with info string `project` containing JSON matching
  `ProjectUpdate` (minus `project_id`, which the backend fills) to propose
  linking assets / updating the goal / updating the flow diagram. Both block
  types (` ```proposal ` and ` ```project `) may appear in one reply → they
  merge into ONE Proposal row (changes may be empty; project_update may be
  null; at least one present, else no proposal).
- **Accept order**: apply file changes first, then the project update, then
  validate that every entry in the new `asset_ids` resolves to an existing
  asset (this ordering lets one proposal create a new skill file AND link it).
  Unknown asset ids → status `failed` with an error naming them.
- **Diagram generation**: one-shot `claude -p` (no session, read-only tools):
  read the asset's file, output ONLY a mermaid flowchart explaining how the
  skill/agent works internally (trigger → steps → outputs/decisions). Cache in
  Postgres keyed by asset_id with the file's sha256; `stale` = stored hash ≠
  current file hash. Regeneration overwrites.
- **DB**: `projects` table; `chat_sessions.project_id` FK ON DELETE CASCADE;
  `proposals.project_update` JSONB; `asset_diagrams` table. One new Alembic
  migration on top of 0001.

---

# API Contract v1.2 — Plugin assets (additive note)

A second provider, **`claude-plugin`**, indexes skills/agents shipped by
installed Claude Code plugins (manifest: `~/.claude/plugins/installed_plugins.json`;
files: `<installPath>/skills/<name>/SKILL.md`, `<installPath>/agents/<name>.md`).

- Asset names are `"{plugin}:{name}"`, so ids can contain extra colons:
  `claude-plugin:skill:vercel:bootstrap`. Id parsing is `split(":", maxsplit=2)`.
- `AssetSummary`/`AssetDetail` gain a required **`read_only: boolean`**.
- Plugin assets are read-only: `updateAsset` returns **403**; proposal file
  changes into plugin directories fail path validation (plugin providers expose
  no writable roots). They ARE searchable, linkable to projects, and diagrams
  can be generated for them.

---

# API Contract v1.3 — Simulations (FROZEN additions)

Additive on top of v1.2. A **simulation** is one dry-run evaluation of a
project: a background `claude -p` reads every linked asset file, walks a
scenario against the project goal, and returns a scored report with concrete
improvement suggestions the user can apply.

## New schemas

```
SimulationChange {              // same shape as ProposalChange
  path: string                  // absolute
  action: "update" | "create" | "delete" | "link" | "unlink"
  new_content: string | null    // full new file content; null for delete
  description: string
  asset_id: string | null       // set when path maps to a known asset
}
SimulationSuggestion {
  title: string
  impact: "high" | "medium" | "low"
  rationale: string             // markdown: why this raises the score
  changes: SimulationChange[]
  status: "pending" | "applied" | "failed"
  error: string | null          // set when status == "failed"
  applied_at: string | null
}
Simulation {
  id: string                    // uuid
  project_id: string
  status: "running" | "completed" | "failed"
  scenario: string              // "" = model derives one from the goal
  score: number | null          // 0-100, null until completed
  verdict: string | null        // one-sentence verdict
  summary: string | null        // markdown: the simulated run, step by step
  analysis: string | null       // markdown: strengths, gaps, failure points
  trace_mermaid: string | null  // mermaid trace of the simulated run
  suggestions: SimulationSuggestion[]
  error: string | null          // set when status == "failed"
  created_at: string
  completed_at: string | null
}
SimulationCreateRequest { scenario?: string }
```

## New endpoints

| Method & path | operation_id | Request | Response |
|---|---|---|---|
| GET `/api/v1/projects/{project_id}/simulations` | `listSimulations` | — | `Simulation[]` (created_at desc) |
| POST `/api/v1/projects/{project_id}/simulations` | `createSimulation` | `SimulationCreateRequest` | `Simulation` (202, status `running`; 409 if one is already running for the project) |
| GET `/api/v1/simulations/{simulation_id}` | `getSimulation` | — | `Simulation` |
| DELETE `/api/v1/simulations/{simulation_id}` | `deleteSimulation` | — | 204 |
| POST `/api/v1/simulations/{simulation_id}/suggestions/{suggestion_index}/apply` | `applySimulationSuggestion` | — | `Simulation` (409 if already applied; `failed` suggestions may be retried) |

## Behavior

- **Run**: POST creates the row and schedules a background one-shot `claude -p`
  (read-only tools, cwd `~/.claude`, `SIMULATION_TIMEOUT_SECONDS` = 900). The
  prompt carries goal, scenario, flow diagram, and every linked asset's id +
  description + file path, and instructs the model to Read each file, simulate
  the scenario step by step, score it against a fixed rubric, and end with one
  fenced ```simulation JSON block (score, verdict, summary, analysis,
  trace_mermaid, suggestions). The frontend polls the list while `running`.
- **Parsing**: last ```simulation block wins; malformed block → status
  `failed`. Score clamped to 0-100. Suggestion changes are validated with the
  proposal-change rules at parse time.
- **Apply**: per-suggestion. Paths re-validated against the writable provider
  roots (plugin files can never be touched); all paths validated before any
  write. Failure marks the suggestion `failed` with `error` (HTTP still 200);
  success marks it `applied` with `applied_at`.
- **Auto-link on apply**: a successful apply syncs the project's `asset_ids`
  with the suggestion's changes — created/updated assets are linked, deleted
  assets unlinked — so the next run always evaluates the actual toolkit.
- **Restart sweep**: backend startup marks any `running` simulation `failed`
  (a restart orphans the in-flight background task).
- **DB**: `simulations` table, FK → projects ON DELETE CASCADE; suggestions
  live denormalized in JSONB with their per-suggestion apply state. Alembic
  migration 0003.

---

# API Contract v1.4 — Simulation scenarios (FROZEN additions)

Additive on top of v1.3. A project now persists the last simulation **scenario**
used or generated, and scenarios can be generated on demand.

## Changed schemas

```
Project += { scenario: string }                 // last simulation scenario used/
                                                // generated; markdown/plain, "" default
ProjectUpdateRequest += { scenario?: string }   // partial; omitted/null = unchanged
```

`ProjectUpdate` (the chatbot proposal payload) and the ` ```project ` chat block
are UNCHANGED — scenarios are not proposed by the chatbot.

## New schemas

```
ScenarioGenerateResponse { scenario: string }   // the generated scenario, saved on the project
```

## New endpoint

| Method & path | operation_id | Request | Response |
|---|---|---|---|
| POST `/api/v1/projects/{project_id}/simulations/scenario` | `generateSimulationScenario` | — | `ScenarioGenerateResponse` (synchronous `claude -p`, up to `SIMULATION_TIMEOUT_SECONDS` = 900 s; 404 unknown project; 502 when the CLI fails or returns nothing) |

## Behavior

- **Generate**: synchronous one-shot `claude -p` (read-only tools, cwd `~/.claude`,
  simulation timeout). The prompt carries the project name, goal, flow diagram, and
  every linked asset's id + description + file path, and asks for ONE concrete
  first-person scenario (2-5 sentences, names invented-but-plausible specifics,
  includes at least one complication, does not restate the goal) in a single fenced
  ` ```scenario ` block. The block is preferred; if absent, the stripped whole reply
  is used; an empty result → 502. The result is saved to `projects.scenario`
  (bumping `updated_at`) and returned.
- **Last-used mirror**: `createSimulation` now also writes the run's scenario
  (trimmed, including the empty string) onto `projects.scenario`, so the tab always
  reflects the most recent scenario.
- **DB**: `projects.scenario` TEXT NOT NULL DEFAULT ''. Alembic migration 0004.

---

# API Contract v1.5 — Autopilot & change summary (FROZEN additions)

Additive on top of v1.4. **Autopilot** chains simulations: run → auto-apply every
suggestion → run again, up to N times. **Change summary** digests every applied
asset change of a project into one generated markdown report.

## Changed schemas

```
Simulation += {
  autopilot_run_id: string | null    // uuid; set when the run is an autopilot iteration
  autopilot_iteration: number | null // 1-based
  autopilot_total: number | null     // requested iteration cap
}
Project += {
  change_summary: string | null      // last generated digest; markdown
  change_summary_at: string | null   // when it was generated
}
```

## New schemas

```
AutopilotCreateRequest { scenario?: string, iterations?: number }  // 1-20, default 5
ProjectSummaryResponse { summary: string, generated_at: string }
```

## New endpoints

| Method & path | operation_id | Request | Response |
|---|---|---|---|
| POST `/api/v1/projects/{project_id}/simulations/autopilot` | `startSimulationAutopilot` | `AutopilotCreateRequest` | `Simulation` (202, iteration 1, status `running`; 409 if one is already running for the project) |
| POST `/api/v1/simulations/autopilot/{run_id}/stop` | `stopSimulationAutopilot` | — | 204 (404 when no run with this id is in flight) |
| POST `/api/v1/projects/{project_id}/summary` | `generateProjectSummary` | — | `ProjectSummaryResponse` (synchronous `claude -p`, `CLAUDE_TIMEOUT_SECONDS`; 404 unknown project; 502 CLI failure) |

## Behavior

- **Autopilot loop** (background task): each iteration rebuilds the simulation
  prompt from the CURRENT project (applied suggestions change files and links),
  runs it, then — unless it is the last iteration — creates the next `running`
  row in the SAME commit that completes the current one (so the project always
  has a running row until the chain ends: polling keeps working, manual runs
  stay blocked) and auto-applies every suggestion of the completed run.
- **Stop conditions**: a run fails; a run yields zero suggestions; no suggestion
  could be applied (the pre-created next row is marked `failed` with an
  explanatory error); the user calls the stop endpoint (takes effect after the
  current run; its suggestions stay `pending`); the iteration cap is reached
  (the final run's suggestions also stay `pending` for manual review).
- **Stop flag** lives in process memory — a backend restart kills the loop and
  the startup sweep marks the running row `failed`, same as manual runs.
- **Summary**: collects every APPLIED change — chat proposals of the project's
  sessions (join via messages) and simulation suggestions — groups them per
  asset, and asks claude for a ```summary block: `## Overview` (direction +
  totals) then `## Changes per asset` (per-asset subsections, most-changed
  first). Zero applied changes short-circuits to a stock line without calling
  claude. Result persisted on `projects.change_summary/_at` (bumps
  `updated_at`) and returned.
- **DB**: `simulations.autopilot_run_id/iteration/total`,
  `projects.change_summary/_at`. Alembic migration 0005.

---

# API Contract v1.6 — Run stats & trigger guide (FROZEN additions)

Additive on top of v1.5. Simulations now persist the **run metadata** the
claude CLI reports (model, duration, tokens, cost), and a project can generate
a **trigger guide** — how to phrase Claude Code prompts so the toolkit fires.

## Changed schemas

```
Simulation += { stats: SimulationStats | null }   // null for pre-v1.6 runs
Project += {
  trigger_guide: string | null      // last generated guide; markdown
  trigger_guide_at: string | null
}
```

## New schemas

```
SimulationStats {                   // every field best-effort, may be null
  model: string | null              // modelUsage keys, " + "-joined
  duration_ms: number | null
  num_turns: number | null
  cost_usd: number | null           // total_cost_usd as reported by the CLI
  input_tokens: number | null
  output_tokens: number | null
  cache_read_tokens: number | null
  cache_creation_tokens: number | null
}
ProjectTriggerResponse { trigger_guide: string, generated_at: string }
```

## New endpoint

| Method & path | operation_id | Request | Response |
|---|---|---|---|
| POST `/api/v1/projects/{project_id}/trigger` | `generateProjectTrigger` | — | `ProjectTriggerResponse` (synchronous `claude -p` with the simulation timeout — it Reads every linked asset file; 404 unknown project; 502 CLI failure) |

## Behavior

- **Stats**: `ClaudeRunner` parses `duration_ms`, `num_turns`,
  `total_cost_usd`, `usage.{input,output,cache_read,cache_creation}` tokens and
  the `modelUsage` model ids from the CLI result JSON into `ClaudeResult.stats`.
  Simulations persist them on completion AND on parse-failure (the cost was
  still incurred); CLI errors leave stats null. Other one-shot callers
  (scenario/summary/diagram/trigger) don't persist stats.
- **Trigger guide**: the prompt lists every linked asset (id + description +
  file path) and instructs the model to Read each file, then produce a fenced
  ```trigger block with fixed sections: `## Entry point`, `## Prompts that
  trigger the full flow` (2-3 paste-ready prompts in code fences), `## Trigger
  phrases per asset` (quoting real description text), `## How the chain runs`,
  `## Make triggering reliable`. Block preferred, stripped reply fallback,
  empty → 502. Zero linked assets short-circuits to a stock line without
  calling claude. Persisted on `projects.trigger_guide/_at` (bumps
  `updated_at`).
- **DB**: `simulations.stats` JSONB, `projects.trigger_guide/_at`. Alembic
  migration 0006.

---

# API Contract v1.7 — Checklist scoring & run memory (FROZEN additions)

Additive on top of v1.6. A run's **score is now computed** from a per-scenario
**capability checklist** instead of a holistic judge number, and each run is
given **memory of the previous run of the same scenario** so it re-grades a
stable checklist rather than re-inventing one, and stops re-suggesting fixes
that already landed. This makes the score comparable across runs and monotone
under applied fixes; it also stops environmental / human-gated capabilities
(`na` items) from permanently capping the score.

## Changed schemas

```
Simulation += { checklist: SimulationChecklistItem[] }   // [] for pre-v1.7 runs
```

## New schemas

```
SimulationChecklistItem {
  id: string                  // stable snake_case id, carried across runs
  title: string               // the capability being graded
  weight: number              // integer 1-3, importance to the goal
  status: "pass" | "partial" | "fail" | "na"
  evidence: string            // asset/file that covers it, or the gap
}
```

## Behavior

- **Score**: computed as `round(100 * Σ(weight·value) / Σ(weight))` over items
  whose status ≠ `na`, where pass=1.0, partial=0.5, fail=0.0. If a reply carries
  no checklist (or every item is `na`), the score falls back to the model's own
  holistic number — so pre-v1.7 rows and old-shape replies still score. The
  model still emits a `score` field, but the backend overrides it.
- **Checklist parsing**: best-effort and additive — a malformed item is skipped,
  not fatal to the block. `weight` clamped to 1-3; an invalid `status` becomes
  `fail` (conservative).
- **Run memory**: on each run the prompt is given the most recent completed run
  of the **same scenario text** (`latest_completed_for_scenario`): its score,
  its checklist (to re-grade with identical `id`/`title`/`weight`), and the
  titles of suggestions applied since. The judge is told to verify those landed
  and only raise NEW issues. Autopilot feeds each iteration the prior iteration
  as memory. A first run of a scenario derives the checklist from scratch.
- **Suggestions**: every suggestion must target a `partial`/`fail` item; when
  every gradable item passes, an empty suggestion list is the expected outcome.
- **DB**: `simulations.checklist` JSONB (nullable). Alembic migration 0007.

---

# API Contract v1.8 — Global CLAUDE.md (FROZEN additions)

Additive on top of v1.7. The global instructions file (`~/.claude/CLAUDE.md`)
is now viewable and editable in the app. It is **not** an asset: it lives
outside the provider roots, so it never appears in `/assets` and chat proposals
can never write it (apply-time path validation still only accepts provider
roots). It has its own singleton endpoint instead.

## New schemas

```
InstructionsDoc {
  path: string              // absolute path to the global CLAUDE.md
  content: string           // full markdown, "" when the file does not exist
  exists: boolean           // false when nothing is on disk yet; PUT creates it
  updated_at: string | null // file mtime, null when absent
}
InstructionsUpdateRequest { content: string }
```

## New endpoints

| Method & path | operation_id | Request | Response |
|---|---|---|---|
| GET `/api/v1/instructions` | `getInstructions` | — | `InstructionsDoc` (200 with `exists: false` when the file is missing — never 404) |
| PUT `/api/v1/instructions` | `updateInstructions` | `InstructionsUpdateRequest` | `InstructionsDoc` (creates the file and any missing parent dirs; 500 on an unreadable/unwritable path) |

## Behavior

- The path comes from settings (`CLAUDE_INSTRUCTIONS_FILE`, default
  `~/.claude/CLAUDE.md`), never from the request — there is exactly one
  document, so there is no id to validate and nothing else can be written.
- A missing file is a normal state, not an error: the UI shows a "create it"
  empty state and the first save writes the file.

# API Contract v1.9 — Scored link suggestions & unlink (FROZEN additions)

Two changes to how a project's toolkit is chosen. **Suggest-links** now scores
every asset it lists 0-100 instead of returning a flat recommended set, so the
cut line between "link this" and "probably not" is visible. **Simulations** can
now propose dropping a linked asset, closing the loop that previously only added.

## Changed schemas

```
SuggestedLink {
  asset_id: string
  reason: string            // one line: why the goal needs it, or why it is borderline
  confidence: number        // 0-100, NEW. >=60 recommended, 40-59 borderline
}
SimulationChange {
  action: "update" | "create" | "delete" | "link" | "unlink"   // "unlink" is NEW
  ...
}
```

## New endpoint

| Method & path | operation_id | Request | Response |
|---|---|---|---|
| POST `/api/v1/projects/{project_id}/suggest-links` | `suggestProjectLinks` | — | `ProjectSuggestLinksResponse` (synchronous `claude -p`; persists nothing; 404 unknown project; 502 CLI failure, no `links` block, or zero known asset ids) |

## Behavior

- **Confidence**: the prompt defines the bands (85-100 load-bearing, 70-84 main
  path, 60-69 adjacent, 40-59 borderline, below 40 omitted) and asks for the
  borderline candidates to be listed too, so the user sees what was considered
  and rejected. The backend re-sorts by confidence descending rather than
  trusting the model's ordering; ties keep the model's order. An omitted or
  unparseable `confidence` defaults to 70 — inside the recommended band, so a
  model that skips the field still yields a usable toolkit.
- **Pre-check threshold**: the dialog checks only `confidence >= 60`. Everything
  else stays listed and unchecked with its score badge and reason.
- **Unlink**: validated like `link` (must map to a known asset, skips the
  writable-roots check, writes nothing) and removes the asset from
  `project.asset_ids` on apply. The file is untouched and other projects keep
  their link — toolkit membership is per-project, which is why `unlink` (like
  `link`) never appears in another project's cross-change alerts.
- **Prompt rule**: unlink is proposed for a linked asset that serves no
  checklist capability and never appears in the trace. Unlink-only suggestions
  are exempt from the "every suggestion must target a partial/fail item" rule.

---

# API Contract v1.10 — Asset-scoped chat (FROZEN additions)

Additive on top of v1.9.

## Changed schemas

```
ChatSession       += { asset_id: string | null }   // null = not asset-scoped
ChatSessionCreateRequest += { asset_id?: string | null }   // e.g. "claude:agent:architect"
```

`listChatSessions` gains an optional `asset_id` query param: when present it
returns that asset's sessions and takes precedence over `project_id`.
`project_id="none"` (global) now excludes asset-scoped sessions, so an asset
chat never shows up in the global chat list.

`createChatSession` 404s on an `asset_id` that no provider resolves.

## Behavior

- **Asset-scoped chat**: a session with `asset_id` gets an extended first system
  prompt — the asset's id, kind, title, description, path, editable/read-only
  status, and its current file content (truncated at 8 000 chars) — plus
  instructions to answer about that asset and to re-Read the file before
  proposing changes. Every user prompt is prefixed with a
  `[current asset: id=…; path=…]` state line, since `--resume` reuses the
  original system prompt.
- **Deleted asset**: if the asset no longer resolves, the exchange still runs,
  unscoped, so the chat history stays readable.
- **DB**: `chat_sessions.asset_id` (nullable, indexed string — assets live on
  disk, so it is not an FK).

---

# API Contract v1.11 — Autopilot scenario rotation (behavior change)

No schema or endpoint changes. Only `AutopilotCreateRequest.iterations`'
description text changed.

## Behavior

- **Perfect run rotates the scenario**: when an autopilot iteration scores 100,
  the chain no longer ends on "zero suggestions". It creates the next `running`
  row as usual, then generates a new scenario with the same prompt as
  `generateSimulationScenario` (the spent one is passed as the previous
  scenario, so the model must write a different story), writes it to
  `projects.scenario` AND to the pre-created next row's `scenario`, and
  continues from there. Any suggestions the perfect run did emit are still
  applied first, but an empty apply is no longer a stop condition for that
  iteration.
- **Why**: a 100 means every question the frozen rubric knows to ask is
  answered — the same reason `control_run` is forced after a 100. Re-running the
  spent scenario burns iterations; a fresh one is what actually finds new gaps.
- **Fresh checklist follows for free**: the new scenario has no completed run,
  so the next iteration is a first run and derives its checklist from scratch.
- **New stop condition**: the scenario could not be generated (CLI failure or an
  empty reply). The pre-created next row is marked `failed` with
  `autopilot stopped: could not generate a new scenario after a perfect score: …`.

---

# API Contract v1.12 — Claude Code session observability (FROZEN additions)

Additive on top of v1.11. Claude Code hooks post their firings to the backend,
which keeps one row per session plus its raw event stream; the Sessions screen
reads them back and polls live through an integer cursor.

## New schemas

```
HookEventRequest {
  session_id: string                  // Claude Code session id
  event_type: string                  // "PreToolUse", "Stop", … — free string, never validated
  cwd?: string | null                 // used on first sight of the session
  model?: string | null               // latest value wins
  tool_name?: string | null
  payload?: object | null             // free-form hook input
  stats?: object | null               // free-form counters, shallow-merged into the session
  ended?: boolean                     // default false; true stamps ended_at
}

CodingSession {
  id: string                          // the Claude Code session id, not a uuid
  cwd: string                         // "" if no event carried one
  git_repo: string | null             // repo folder name derived from cwd
  model: string | null
  source: string                      // "claude-code"
  started_at: string
  last_event_at: string
  ended_at: string | null
  stats: object | null                // merged free-form counters
  event_count: number                 // derived
  tool_call_count: number             // derived: events with event_type == "PostToolUse"
  duration_seconds: number            // derived: started_at → ended_at ?? last_event_at
}

CodingEvent {
  id: number                          // monotonic; the poll cursor
  session_id: string
  event_type: string
  tool_name: string | null
  payload: object | null
  created_at: string
}
```

## New endpoints

| Method & path | operation_id | Request | Response |
|---|---|---|---|
| POST `/api/v1/hooks/events` | `ingestHookEvent` | `HookEventRequest` | `204` (no body; 422 only when `session_id` or `event_type` is missing/empty) |
| GET `/api/v1/coding-sessions?limit=50&offset=0&include_empty=false&include_automated=false` | `listCodingSessions` | query: `limit` 1-200, `offset` >= 0, `include_empty` bool, `include_automated` bool | `CodingSession[]` (last_event_at desc) |
| GET `/api/v1/coding-sessions/{session_id}` | `getCodingSession` | — | `CodingSession` (404 if unknown) |
| GET `/api/v1/coding-sessions/{session_id}/events?after=0&limit=500` | `listCodingSessionEvents` | query: `after` >= 0, `limit` 1-1000 | `CodingEvent[]` (id asc; 404 unknown session) |

## Behavior

- **Ingest is an upsert plus an insert, and nothing else.** It sits in the
  critical path of every hook firing, so no CLI call, no network, and a
  filesystem touch only on the first event of a session. First sight creates the
  session row from `cwd`/`model`; every event bumps `last_event_at`; `ended:
  true` stamps `ended_at`. `cwd` is kept from the first event that carried one
  (a session's directory does not move), `model` takes the newest value (`/model`
  mid-session).
- **Empty sessions are hidden from the list.** The Claude desktop app spawns a
  headless `claude` per open directory and discards it, producing a `SessionStart`
  (plus a `SessionEnd`, when the async hook outlives the process) with no turn and
  no transcript — about three quarters of all rows. `listCodingSessions` therefore
  leaves out any session with no `UserPromptSubmit` and no `PostToolUse` that is
  also finished: `ended_at` set, **or** silent for more than 2 minutes, since a
  ghost is not reliably closed. A session that was prompted or ran a tool is never
  hidden, however long it then goes quiet, and neither is one in its first two
  minutes — that is indistinguishable from a real session starting up. Hidden is
  not dropped: ingest still stores it, `getCodingSession` still serves it, and
  `include_empty=true` puts it back in the list.
- **Automated runs are labelled and hidden by default.** The `SessionStart` hook
  reports the launcher chain in `payload.launched_by`; if any ancestor is a
  `claude` invoked with `-p`/`--print`, the run had no one at the keyboard — a
  wrapper script, a hook, a scheduler — and the session is stored with
  `launch_mode: "automated"`. A chain without that flag gives `"interactive"`; no
  chain at all (every session recorded before the hook shipped) leaves it null.
  `listCodingSessions` omits `automated` rows unless `include_automated=true`;
  null is treated as unknown and always listed. The field is on `CodingSession`,
  so the detail view can badge it.
- **Tolerant by design**: `event_type` is a free string with no enum, so a hook
  type that does not exist yet still records. Over-long values are truncated to
  the column width rather than rejected — a hook must never fail a Claude Code
  run. Only a missing/empty `session_id` or `event_type` is a 422.
- **Size cap**: `payload` and `stats` are capped at 32 768 serialized characters
  each. Past that the stored value becomes
  `{_truncated: true, _chars: <n>, _preview: <first 2 000 chars>}`, so one
  runaway hook cannot bloat the database. `stats` is shallow-merged (newest key
  wins) and the merged result is capped the same way.
- **git_repo**: the name of the nearest ancestor directory of `cwd` containing a
  `.git`, resolved once on first sight. No subprocess, no remote lookup; null
  outside a repo.
- **Cursor**: `coding_events.id` is a plain autoincrementing integer, which is
  what makes history and live polling the same query —
  `WHERE session_id = ? AND id > ? ORDER BY id LIMIT ?`. The client keeps the
  last id it holds and passes it as `after`; `after=0` loads from the start.
- **Derived fields** are computed per request, not stored: counts by one grouped
  aggregate over `coding_events`, duration in Python (SQLite and Postgres have no
  shared interval arithmetic).
- **No auth**, like the rest of this API — single user, localhost.
- **DB**: `coding_sessions` (session id as TEXT PK) and `coding_events`
  (autoincrement PK, FK → coding_sessions ON DELETE CASCADE, index on
  `(session_id, id)`; `coding_sessions.last_event_at` indexed for the list
  order). Alembic migration 0012.

---

# API Contract v1.13 — Run-centric coding sessions (FROZEN additions)

Additive on top of v1.12. A session stops being a flat event log and becomes a
**run**: a request, an outcome, a cost, a set of **agent lanes**, and a sequence
of **phases** on a time axis. Phase, agent, cost and context used to live inside
`coding_events.payload`; they are rows now, so a card grid and a per-lane
waterfall both render without the client parsing JSON.

## Changed schemas

```
CodingSession += {
  title: string | null                // the run's request: first prompt, or the factory's
  workflow: string | null             // "factory"; null (or "chat") = plain Claude Code session
  status: string                      // running | success | failed | interrupted
  cost_usd: number | null
  tokens_total: number | null
  tokens_in: number | null
  tokens_out: number | null
  cache_read_tokens: number | null
  phases: PhaseSummary[]              // ordered by seq — enough to draw the card's lane chart
  agents: AgentLane[]                 // ordered by first appearance
}

CodingEvent += {
  phase_id: number | null             // the phase this event happened in
  agent: string | null                // the lane it happened in
  ok: boolean | null                  // did the reported thing succeed
  duration_ms: number | null
  ended_at: string | null             // when the reported work finished; span = ended_at - duration_ms
}

HookEventRequest += {
  title?: string | null
  workflow?: string | null
  status?: string | null
  phase?: PhaseIn | string | null     // a bare string means { name: <string> }
  agent?: AgentIn | string | null     // idem
  ok?: boolean | null
  duration_ms?: number | null
}
```

## New schemas

```
PhaseIn {                             // every field optional; absent never clears
  name?, kind?, agent?, description?, status?, commit_sha?: string | null
  seq?, duration_ms?, tokens_in?, tokens_out?, corrections?: number | null
  cost_usd?: number | null
}

AgentIn {                             // every field optional; absent never clears
  name?, model?, color?: string | null
  context_tokens?, context_window?, cost_usd?, tokens_in?, tokens_out?: number | null
}

PhaseSummary {                        // what a card needs, and nothing else
  seq: number                         // position in the run
  name: string                        // plan | build | checks | review | document | "turn 3" | …
  agent: string | null                // lane owner
  status: string                      // running | passed | failed | skipped | abandoned
  started_at: string
  duration_ms: number | null          // reported, or started_at → ended_at
}

CodingPhase extends PhaseSummary {    // the detail waterfall's row
  id: number                          // what CodingEvent.phase_id points at
  kind: string | null                 // engineer | agent | code | git
  description: string | null
  ended_at: string | null             // null while running
  cost_usd: number | null
  tokens_in: number | null
  tokens_out: number | null
  corrections: number                 // retries this stage cost
  commit_sha: string | null
  gates_passed: number
  gates_failed: number
}

AgentLane {
  name: string                        // "main", a subagent type, or a pipeline stage
  model: string | null
  color: string | null
  context_tokens: number | null       // with context_window, the context bar
  context_window: number | null       // null when nobody reported one
  cost_usd: number | null
  tokens_in: number | null
  tokens_out: number | null
  turns: number
}

CodingSessionDetail extends CodingSession {
  phases: CodingPhase[]               // whole rows instead of card summaries
}
```

## Changed endpoints

| Method & path | operation_id | Request | Response |
|---|---|---|---|
| POST `/api/v1/hooks/events` | `ingestHookEvent` | `HookEventRequest` (all new fields optional) | `204` (unchanged: 422 only when `session_id` or `event_type` is missing/empty) |
| GET `/api/v1/coding-sessions?limit=50&offset=0&include_empty=false&include_automated=false&workflow=&status=` | `listCodingSessions` | query: v1.12's four, plus `workflow` and `status` | `CodingSession[]` (last_event_at desc) |
| GET `/api/v1/coding-sessions/{session_id}` | `getCodingSession` | — | **`CodingSessionDetail`** (was `CodingSession`; 404 if unknown) |
| GET `/api/v1/coding-sessions/{session_id}/events?after=0&limit=500` | `listCodingSessionEvents` | unchanged | `CodingEvent[]` (id asc, now with the five new fields) |

## Behavior

- **The ingest stays additive and unfailable.** A v1.12 body behaves exactly as
  it did. New fields are optional, unknown keys are ignored, and — new in v1.13
  — an optional field whose value cannot be validated is **dropped rather than
  422'd**, so a hook never fails because the backend evolved and it did not.
  `session_id` and `event_type` remain the only 422.
- **Upsert, partially.** `phase` is upserted on `(session_id, seq)` when a seq
  is given and on `(session_id, name)` otherwise, appending a new seq when
  neither matches; `agent` is upserted on `(session_id, name)`. An absent field
  is silence, never an instruction to clear — which is what lets one event open
  a stage and a later one close it. `gates_passed`/`gates_failed` and a lane's
  `turns` are the exception: they are counted across events, not reported as
  totals. A stage that reaches a terminal status
  (`passed`/`failed`/`skipped`/`abandoned`) gets `ended_at` stamped, and a
  `duration_ms` computed from `started_at` if the producer did not report one.
- **A turn cannot outlive the next one on its lane.** A dropped `Stop` hook
  leaves a `main` turn open, which would otherwise claim the rest of the run and
  sit under every later turn. The prompt that opens the next `main` turn closes
  the previous one as `abandoned` — it ended, but not when. Subagent lanes are
  left alone: several agents of one type run at once, so two open turns there
  are two agents working.
- **The event is linked to the stage it happened in.** With a `phase` block, to
  that stage; without one, to whichever stage of the run is still open. `agent`,
  `ok` and `duration_ms` are stored on the event, and `ended_at` is set equal to
  `created_at` when a duration was reported — a hook reports a duration for work
  that has just finished, so the span runs `ended_at - duration_ms → ended_at`.
- **The run's totals are the sum of its phases**, recomputed whenever a phase is
  written, plus any `stats` key that has a column of its own (`cost_usd`,
  `total_cost_usd`, `tokens_total`/`total_tokens`, `tokens_in`/`input_tokens`,
  `tokens_out`/`output_tokens`, `cache_read_tokens`/`cache_read_input_tokens`).
  `stats` itself is unchanged — still the free-form overflow, still shallow-merged.
- **Derivation for a plain Claude Code session.** Its hooks name no stage and no
  lane, so the backend synthesizes both, incrementally, during ingest — never in
  the frontend: lane `main` (running the session's model) plus one lane per
  distinct subagent type seen (a `Task`/`Agent` call's `tool_input.subagent_type`,
  or a `SubagentStop`'s `agent_type`, or that stop's transcript sidecar, or the
  literal `subagent` when nothing could name it — more than half of real stops
  carry only a transcript path, and dropping them left their turns attributed to
  nobody); one phase per round trip, `passed` once closed and `running` until
  then, kind `agent`. Two round trips produce one: `UserPromptSubmit` → `Stop` on
  `main`, named `turn N`, and `PreToolUse` on the spawn tool → `SubagentStop` on
  the subagent's own lane, named after the call's `description`. **N counts that
  lane's phases, not `seq`** — a span opening between two prompts takes a `seq`,
  so `seq` stopped being able to double as the turn number. A `Stop` closes only
  `main`'s stage and a `SubagentStop` only its own lane's, since a subagent runs
  *alongside* a turn rather than inside one; likewise an unlabelled event lands
  on its own lane's open stage, falling back to the newest open one only for a
  producer that names stages but no lanes. A `SubagentStop` with no stage open on
  its lane — every session recorded before the `PreToolUse` hook existed — gets a
  zero-length stage stamped at the moment it ended, described `start not
  recorded`: the honest shape of an end without a start, and the reason those
  lanes used to render as blank rows. `title` from the first prompt, truncated to
  300 characters; `status` `success` on `SessionEnd` (or any `ended: true`),
  running while open. A session that only ever emitted lifecycle events — three
  quarters of all rows — gets no lanes and no phases at all.
  The `PreToolUse` hook is subscribed for `Task|Agent` alone: it is the only
  event that knows when a subagent *started* (`PostToolUse` fires when the call
  returns, which for a background agent is long before the agent is done), and
  matching every tool would double the event stream to learn nothing.
- **Derivation for a factory run.** The pipeline runner predates these fields
  and reports its stage and lane inside `payload` (`{event, phase, agent,
  result, detail, cost_usd, tokens_in, tokens_out, duration_ms}`); an event
  whose payload echoes its own `event_type` and names a `phase` is read that
  way, so an unchanged runner populates the same tables. Its synthetic `run`
  phase is the run envelope, not a stage: the `phase_start` detail becomes the
  session `title`, and `run_end` sets `status` from `result` and promotes its
  `stats`. `phase_start` decides a stage's `kind` (`agent` when a lane owns it,
  `code` when none does), `phase_end` its status, duration, cost, corrections
  and commit; `agent_turn` accumulates the stage's tokens and the lane's turns,
  cost and context; `gate_pass`/`gate_fail` count. Explicit `phase`/`agent`
  blocks always outrank what the payload implies.
- **Filters**: `workflow=factory` matches only pipeline runs; `workflow=chat`
  matches plain sessions **and** the ones that never claimed a workflow, since
  nothing writes `"chat"`. `status` is exact equality. Both compose with
  `include_empty` and `include_automated`.
- **Rebuild**: `service.backfill_session(db, session_id)` replays a session's
  stored events through the same derivation the live ingest uses, which is what
  gives a pre-v1.13 session the same shape as a new run. It is idempotent by
  construction — the derived rows are dropped and rebuilt rather than updated,
  so the counters do not double — and it unlinks events explicitly rather than
  trusting `ON DELETE SET NULL`, which SQLite only enforces on request.
- **DB**: `coding_sessions` gains `title`, `workflow`, `status` (NOT NULL
  DEFAULT `running`), `cost_usd`, `tokens_total`, `tokens_in`, `tokens_out`,
  `cache_read_tokens`. New `coding_phases` (autoincrement PK, FK →
  coding_sessions ON DELETE CASCADE, UNIQUE `(session_id, seq)`) and
  `coding_agents` (same FK, UNIQUE `(session_id, name)`). `coding_events` gains
  `phase_id` (FK → coding_phases ON DELETE SET NULL), `agent`, `ok`,
  `duration_ms`, `ended_at`; its `(session_id, id)` index is untouched and stays
  the poll cursor. Alembic migration **0014_coding_run_model** — 0013 was
  already taken by `launch_mode`, which the v1.12 section documents.

---

# API Contract v1.14 — Honest runs and asset attribution (FROZEN additions)

Additive on top of v1.13, and the reason the feature exists: a run now reports
**which skills and which subagents it actually used**, and stops lying about
what it is doing and how long it took.

## New schemas

```
AssetUse {                            // one asset, as one run used it
  kind: string                        // "skill" | "agent"
  name: string
  asset_id: string                    // "claude:skill:<name>" / "claude:agent:<name>"
  lane: string | null                 // the lane that used it
  uses: number
}

CodingAssetUsage {                    // the same asset, across every run
  kind: string
  name: string
  asset_id: string
  sessions: number                    // distinct runs that used it
  uses: number
  last_used_at: string
}
```

## Changed schemas

```
CodingSession += {
  title_source: string | null         // prompt | factory | provenance | cwd; null when untitled
  parent_session_id: string | null    // the run that launched this one
  child_count: number                 // runs this one launched
  active_ms: number                   // time actually working — lead with this
  wall_ms: number                     // duration_seconds in ms; the clock on the wall
  assets: AssetUse[]                  // most-used first
}

CodingSession.status                  // now also "abandoned" — derived, never stored
```

## Changed and new endpoints

| Method & path | operation_id | Request | Response |
|---|---|---|---|
| GET `/api/v1/coding-sessions?…&roots_only=false` | `listCodingSessions` | v1.13's six, plus `roots_only` bool | `CodingSession[]` (**live first**, then last_event_at desc) |
| GET `/api/v1/coding-sessions/{session_id}` | `getCodingSession` | — | `CodingSessionDetail` (unchanged shape, new fields) |
| GET `/api/v1/coding-assets?since=&kind=` | `listCodingAssetUsage` | query: `since` datetime, `kind` `skill`\|`agent` | `CodingAssetUsage[]` (uses desc, then name) |

## Behavior

- **`running` now means live, and nothing else.** `SessionEnd` rides an async
  hook that the dying process outruns, so most runs never close themselves — 78
  of 113 stored rows claimed `running`, the oldest last heard from a day and a
  half earlier. The stored column is unchanged; the **serializer** derives what
  it reports: a run still stored `running`, with no `ended_at`, whose
  `last_event_at` is older than **2 minutes** (`IDLE_WINDOW`, the same window
  that hides ghosts) reports `status: "abandoned"`. A run that *did* report an
  outcome keeps it however old it is — only the absence of one is filled in from
  silence. One helper (`serializers.derived_status`) serves list and detail, so
  the two can never disagree, and the `status` query filter matches the derived
  value: `status=abandoned` finds the stale ones, `status=running` only the live.
- **`active_ms` is the honest duration.** `wall_ms`/`duration_seconds` measure
  the clock, which turns a closed laptop into a 34-hour session. `active_ms`
  sums the gaps between consecutive events and throws away any gap longer than
  **60 s** (`ACTIVE_GAP`) — a pause is not work. A **factory run prefers the sum
  of its stages' `duration_ms`** when it has any, because the runner measures
  those rather than inferring them. Computed in Python over two loaded columns,
  like `duration_seconds`: SQLite and Postgres share no interval arithmetic.
  Both fields are always present; the UI leads with `active_ms`.
- **Live runs sort first.** `last_event_at DESC` alone buries a run that is
  working right now under one that spoke a minute later and then died. The order
  is: open **and** recent first, then everything by `last_event_at DESC`.
- **Titles have a provenance, and prompt-less runs get one.** `title_source`
  says which signal won, and they are ranked — `factory` > `provenance` >
  `prompt` — with an equal-ranked title never replacing one already stored (the
  *first* prompt is the request; the fifth is a follow-up).
  - `prompt` — the first `UserPromptSubmit`, truncated to 300 characters.
  - `factory` — the pipeline runner's statement of the request: an explicit
    `title` on the hook body, or the run envelope's `detail`.
  - `provenance` — read off the `launched_by` ancestry the `SessionStart` hook
    records. A `factory/run.py` ancestor means this run **is a pipeline stage**:
    the parent is the factory run that owned the same working directory at that
    instant, the stage is the parent's most recently started phase, and the title
    is `"<stage> stage · factory-<run-id>"`. It outranks the prompt on purpose —
    every stage child is prompted with the same wall of boilerplate ("You are the
    BUILD stage of…"), and the provenance name is what identifies it.
  - `cwd` — the last resort for a run with no title at all, derived at read time
    (never stored): the repo or working-directory name, or
    `"headless run · <name>"` when `launch_mode` is `automated`.
- **Parents and children.** `parent_session_id` is set once, at the first event
  carrying a `launched_by` chain, and `child_count` is counted per request. It is
  deliberately **not** a foreign key: a self-referential FK would force SQLite to
  rebuild `coding_sessions` under three child tables, and an unresolvable parent
  should simply leave the child shown as a root. `roots_only=true` hides every
  run that has a parent, so a pipeline's five headless stages collapse into their
  parent instead of showing as five orphan chat cards. Default `false`.
- **Asset attribution — four signals, because the obvious one barely fires.**
  Across 2 237 recorded tool calls there were **two** explicit `Skill` calls and
  **zero** `Task` calls, so path-sniffing and transcript-reading are what make
  the feature real. Counted during ingest, per completed tool call:
  - `tool_name == "Skill"` → skill, named by `payload.tool_input.skill`.
  - `Read` or `Glob` whose target path matches
    `**/.claude/skills/<name>/SKILL.md` → skill `<name>`. This is how a skill
    actually loads. `Edit`/`Write` are excluded: authoring an asset is not using
    one, and `PreToolUse` is excluded because it fires before the permission
    answer.
  - `tool_name` in `{"Task", "Agent"}` → agent, named by
    `payload.tool_input.subagent_type`. Both names, because the harness has
    shipped the spawn tool as each — matching `Task` alone missed every spawn in
    the sessions that actually had them.
  - `SubagentStop` → agent. The hook rarely carries `agent_type`, so the
    `agent_transcript_path` it does carry is used: the sidecar beside it
    (`<transcript>.meta.json`) names the `agentType`. Reading it is the one
    filesystem touch outside a session's first event — legitimate for a
    single-user local tool that already reads `~/.claude`, capped at 64 KB, and
    never able to fail an ingest: a missing, oversized or malformed sidecar
    degrades to the name `"subagent"`.

  `lane` is the event's lane — `main` for a plain session's own tool calls, the
  stage's lane for a pipeline run, null when the event belonged to no lane.
  Names match masterwork's own asset ids (`claude:skill:<name>`,
  `claude:agent:<name>`), served alongside the raw name so a card links straight
  to the asset page. Plugin-provided skills are not path-sniffed.
- **The rollup** groups `coding_assets` by `(kind, name)`: distinct `sessions`,
  total `uses`, and the newest `last_seen_at`, ordered by uses descending with
  the name breaking ties so the ranking is stable. `since` filters on
  `last_seen_at`; `kind` narrows to skills or agents. This is the flywheel view —
  which assets earn their keep.
- **Rebuild**: `service.backfill_session` now clears and replays assets, title
  provenance and the parent link along with phases and lanes, so it stays
  idempotent (uses do not double). `service.backfill_all` replays every stored
  session **oldest first**, which is what lets a pipeline run's stages exist by
  the time its children look for the stage they belong to.
- **DB**: `coding_sessions` gains `title_source` and `parent_session_id` (plain
  indexed column, no FK — see above). New `coding_assets` (autoincrement PK, FK →
  coding_sessions ON DELETE CASCADE, UNIQUE `(session_id, kind, name, lane)`,
  indexes on `(kind, name)` and `last_seen_at`). The unique constraint cannot
  enforce the null-lane case in either dialect, so the upsert matches
  `lane IS NULL` itself. Alembic migration **0015_coding_assets**.

---

# API Contract v1.15 — Asset usage on the asset pages (FROZEN additions)

Additive on top of v1.14. v1.14 answered "what did this run use?"; v1.15 answers
the question from the other end — **who used this skill, and what did they pass
it?** — so the Skills and Agents pages carry their own usage instead of it
living only on the Sessions screen.

## New schemas

```
AssetCall {                           // one recorded call of one asset
  used_at: string
  lane: string | null                 // the lane that made the call
  source: string                      // which signal named it, see below
  input: { [key: string]: string } | null   // the call's arguments, truncated
}

AssetSessionUse {                     // one run that used the asset
  session_id: string
  title: string | null                // derived like the Sessions screen derives it
  git_repo: string | null
  cwd: string
  status: string                      // derived run status, not the stored one
  started_at: string
  uses: number                        // calls this run made, across all its lanes
  first_used_at: string
  last_used_at: string
  calls: AssetCall[]                  // newest first, capped — see below
}
```

## New endpoints

| Method & path | operation_id | Request | Response |
|---|---|---|---|
| GET `/api/v1/coding-assets/{asset_id}/sessions?limit=50&include_inspection=false` | `listAssetSessionUses` | path: asset id slug; query: `limit` 1–200, `include_inspection` bool | `AssetSessionUse[]` (last used desc) |

## Behavior

- **Matched on `(kind, name)`, not on the whole id.** A plugin skill is recorded
  under the name Claude Code calls it by (`"vercel:deploy"`) while its asset id
  names the provider that installed it (`"claude-plugin:skill:vercel:deploy"`),
  so the provider segment is parsed and discarded. A malformed id is still a
  **400**; an id nobody has used is an empty list, not a 404 — the asset exists,
  its usage does not.
- **`source` is the signal that named the use**, and it decides what `input` can
  possibly hold — the four signals of v1.14, now recorded per call:
  - `skill_call` — an explicit `Skill` call. `input.args`.
  - `spawn_call` — a `Task`/`Agent` call. `input.description`, `input.prompt`,
    `input.subagent_type`, `input.model`.
  - `skill_read` — a `SKILL.md` read, which is how a skill actually loads.
    `input.path` only: **there are no arguments**, because there was no call.
  - `subagent_stop` — a finished subagent. `input` is `null`: the hook says the
    agent ran, never what it was asked to do.

  Every value is a string, truncated at **2 000 characters per key** (a spawn's
  `prompt` is a whole brief). Non-string values are JSON-encoded first.
- **`calls` is capped at 200 across the whole response**, not per run: the rows
  only ever back an expanded row in the UI, and one run that read a `SKILL.md` two
  hundred times must not be able to make the response two hundred times bigger.
  So `calls.length` can be smaller than `uses`, and `uses` — summed from
  `coding_assets`, the counter of record — is the number to trust.
- **Inspection runs are excluded by default**, exactly as in the `/coding-assets`
  rollup: masterwork's own analysis passes read every linked asset's `SKILL.md`,
  and counting them would list masterwork as the heaviest user of every skill.
  `include_inspection=true` shows them.
- **A run recorded before v1.15 has an empty `calls`** with a non-zero `uses`:
  the log is derived, and only a `POST /coding-sessions/backfill` replay fills it
  in. The UI says so rather than showing the run as argument-less.
- **DB**: new `coding_asset_uses` (autoincrement PK, FK → coding_sessions ON
  DELETE CASCADE, indexes on `(kind, name)` and `session_id`). No unique
  constraint — it is an append-only log, one row per call, deliberately not
  deduplicated. Dropped and rebuilt by `backfill_session` alongside the other
  derived rows, so replaying twice does not double it. Alembic migration
  **0016_coding_asset_use_log**.

---

# API Contract v1.16 — Observability setup (FROZEN additions)

Additive on top of v1.15, and the first endpoints that write outside
masterwork's own files. v1.12 assumed the hooks were already installed; these
install them, so a fresh `npx masterwork` needs no terminal step.

## New schemas

```
ObservabilityIntegration {
  id: string                          // "claude-code"
  label: string                       // agent name for the UI
  state: "connected" | "outdated" | "disconnected" | "unavailable"
  detail: string                      // one sentence, written for the user
  ingest_url: string                  // where this agent's hooks post
  events: string[]                    // the agent events subscribed once connected
  config_path: string | null          // the agent config file that is edited
  script_path: string | null          // installed forwarder
  backup_path: string | null          // backup of the agent config, null until one is taken
}
```

## New endpoints

| Method & path | operation_id | Request | Response |
|---|---|---|---|
| GET `/api/v1/observability/integrations` | `listObservabilityIntegrations` | — | `ObservabilityIntegration[]` |
| POST `/api/v1/observability/integrations/{integration_id}/connect` | `connectObservabilityIntegration` | — | `ObservabilityIntegration` (404 unknown id, 409 not connectable) |
| POST `/api/v1/observability/integrations/{integration_id}/disconnect` | `disconnectObservabilityIntegration` | — | `ObservabilityIntegration` (404 unknown id, 409 unreadable config) |

## Behavior

- **The four states are what the UI branches on.** `connected` — recording.
  `disconnected` — nothing of ours in the agent's config. `outdated` — our
  entries are there but point at a path that has moved or an older event set;
  `connect` repairs it in place, and the UI offers *Repair* rather than
  *Connect*. `unavailable` — nothing can be done here (the agent has never run
  on this machine, no `python3` on PATH, or a config file we refuse to parse);
  `detail` says which, and `connect` answers **409** instead of guessing.
- **Reading never writes.** `GET` only ever reads the agent's config. The hooks
  are installed on an explicit `connect` and nothing else — not at startup, not
  as a side effect of opening the Sessions screen.
- **`connect` is idempotent and non-destructive.** It backs the agent's config
  up to `<config>.masterwork.bak` before writing, replaces only the hook entries
  whose command runs masterwork's forwarder — a matcher group holding someone
  else's hook alongside ours keeps theirs — and skips the write entirely when
  nothing would change. An event we don't subscribe to is never touched.
- **`disconnect` removes only our entries** and drops an event key when we were
  its only subscriber, so a config returns to its pre-connect shape. Recorded
  sessions are kept: this stops the recording, it does not erase it. Disconnect
  on a never-connected agent creates no file.
- **The forwarder is copied out of the install, not referenced in place.**
  `connect` writes it to `~/.masterwork/hooks/` with a `config.json` naming the
  ingest URL, and records an absolute system `python3` in the command. Under
  `npx masterwork` the package lives in npm's cache; a path into that cache
  would break at the next prune, which is exactly the `outdated` state.
- **`ingest_url` follows the port the API was actually started on**, read from
  `MASTERWORK_API_PORT` (the launcher passes it through). The forwarder resolves
  its target as `MASTERWORK_INGEST_URL` → sidecar `config.json` → the default
  `http://localhost:8008/api/v1/hooks/events`.
- **One integration per agent, resolved by id.** Claude Code is the only one
  today. A second agent is a new `Integration` implementation plus a line in
  `app/observability/registry.py`: the endpoints, schema and UI take it as is.
- **No DB.** Nothing here is stored — the agent's own config file is the record.

---

# API Contract v1.17 — Honest windows, child lookup, and the endpoints nobody wrote down (FROZEN additions)

Additive on top of v1.16, and mostly a correction. Two of the coding-observability
answers were wrong rather than missing — a time window that reported an asset's
whole history, and a child list the frontend had to assemble itself out of a
page of runs — and five endpoints have been shipping undocumented.

## Changed endpoints

| Method & path | operation_id | Request | Response |
|---|---|---|---|
| GET `/api/v1/coding-sessions?…&parent_session_id=` | `listCodingSessions` | v1.14's seven, plus `parent_session_id` string | `CodingSession[]` (shape and order unchanged) |
| GET `/api/v1/coding-assets?since=&kind=&include_inspection=` | `listCodingAssetUsage` | unchanged | `CodingAssetUsage[]` — with `since`, every field now means *inside the window* |

No schema changed, so the generated client only gains one optional query
parameter, appended last.

## Endpoints that shipped without being written down

| Method & path | operation_id | Request | Response |
|---|---|---|---|
| POST `/api/v1/coding-sessions/backfill` | `backfillCodingSessions` | — | `BackfillTotals` |
| POST `/api/v1/coding-sessions/{session_id}/backfill` | `backfillCodingSession` | — | `BackfillResult` (404 unknown session) |
| GET `/api/v1/projects/{project_id}/cross-changes` | `listProjectCrossChanges` | — | `ProjectCrossChangesResponse` (404 unknown project) |
| POST `/api/v1/projects/{project_id}/generality-audit` | `auditProjectGenerality` | — | `ProjectGeneralityResponse` (synchronous `claude -p` with the simulation timeout — it Reads every linked asset file; 404 unknown project; 502 CLI failure) |

## Schemas that shipped without being written down

```
Project += {                          // filled in by auditProjectGenerality
  generality_report: string | null    // last generality audit; markdown
  generality_report_at: string | null // when it was generated
}

BackfillResult {                      // one session rebuilt
  session_id: string
  events: number                      // events replayed through the live derivation
  phases: number                      // stages the replay rebuilt
  agents: number                      // lanes the replay rebuilt
  assets: number                      // asset counter rows the replay rebuilt
}
BackfillTotals { sessions, events, phases, agents, assets: number }

CrossChange {                         // an edit another owner made to a linked asset
  asset_id: string
  action: string                      // update | create | delete
  source: "simulation" | "proposal"
  project_id: string | null           // null when a global (unscoped) chat did it
  project_name: string | null
  title: string                       // the applied suggestion's title, or the proposal's summary
  applied_at: string
}
ProjectCrossChangesResponse {
  since: string | null                // this project's last completed run; null = nothing to invalidate
  changes: CrossChange[]              // newest first
}

ProjectGeneralityResponse { generality_report: string, generated_at: string }
```

## Behavior

- **`since` counts calls, not counters.** `coding_assets` holds one row per
  (run, asset, lane) with a single `last_seen_at`, so filtering it on that
  timestamp and summing `uses` returned the asset's *entire* recorded history
  the moment its most recent call fell inside the window: "last 24h" quoting
  figures from a fortnight ago, with no way for the reader to tell. With `since`
  the rollup is computed from `coding_asset_uses` instead — the append-only log
  v1.15 already added, one row per call — so `uses` counts calls inside the
  window, `sessions` counts the runs that made one, and `last_used_at` is the
  newest of them. Without `since` nothing changes: the counters are exactly
  right for an all-time total and stay the cheaper query. `include_inspection`
  applies identically on both paths, or the ranking would appear to reorder
  itself when the window is switched.
  - The consequence to know: a run recorded **before v1.15** has counters but no
    log rows, so it contributes to the all-time total and nothing to a window
    until `POST /coding-sessions/backfill` replays it. That is the same caveat
    v1.15 states for an empty `calls` list, now visible in the rollup too.
- **`parent_session_id` lists one run's children**, which is the complement of
  `roots_only=true` (children of *nobody*). Composable with `workflow` and
  `status`, and with the same ordering; combined with `roots_only=true` it is a
  contradiction and correctly returns nothing.
  - **This scope ignores `include_empty` and `include_automated`.** Those two
    keep the grid clean, and every pipeline stage child is a `claude -p` one-shot
    that fails both — so with them applied, a parent whose card reads *4 stages*
    would have answered this query with an empty list. A caller naming a parent
    id has already chosen its scope, and what comes back is exactly the
    population the parent's `child_count` counts.
- **`interrupted` is stored, never derived — and the reverse of `abandoned`.**
  v1.14 derives `abandoned` from silence because a run that stops talking has
  told us nothing. A run that was *cut short* leaves identical evidence: a killed
  process, a lost hook and a closed laptop are indistinguishable from the event
  stream, so masterwork does not guess, and nothing in it ever writes
  `interrupted`. It stays accepted on the hook body from a producer that knows
  better (the factory reports an aborted run as `failed` today, so nothing sends
  it yet), the `status` filter matches only such reported rows — an empty list
  rather than a plausible one — and the query parameter's description says so.
  - A body-reported status now **survives a rebuild**. Only `payload` is stored,
    so a replay cannot re-derive a status the producer stated as a top-level
    field; before this, one `POST /{id}/backfill` silently turned a reported
    `interrupted` into `success`. The replay still wins whenever it derives a
    status of its own (a factory `run_end` does).
- **The backfill endpoints are the only way to rebuild derived rows.** Stages,
  lanes, assets, the call log, the title provenance and the parent link are all
  derived, so improving a derivation does nothing for a run already recorded
  until its events are replayed through it. `POST /coding-sessions/{id}/backfill`
  replays one, `POST /coding-sessions/backfill` replays every stored session
  **oldest first** — the order that lets a pipeline run's stages exist by the
  time its children look for the stage they belong to. Both are idempotent by
  construction: the derived rows are dropped and rebuilt rather than updated,
  which is what stops the counters doubling. The event stream is never touched —
  it is the record this rebuilds from.
- **`listProjectCrossChanges` says whether a project's score is stale.** A
  project was scored against its linked asset files as they were at its last
  completed simulation; other projects and global chats edit the same shared
  files. This lists every applied change to a linked asset since that run —
  from an applied simulation suggestion or an accepted chat proposal — so the UI
  can offer *re-run*. `link`/`unlink` changes are left out: they alter someone
  else's toolkit, not the file. A project with no completed run has no score to
  invalidate and answers `since: null` with an empty list. Pure DB read, no
  `claude` call, nothing persisted.
- **`auditProjectGenerality` asks whether the toolkit has been overfitted.** A
  long series of simulations on one scenario quietly leaks that scenario's domain
  into shared assets. The audit reads every linked asset file and reports where
  that has happened; the markdown is saved on the project
  (`generality_report`/`_at`), so the page can render the last one without
  re-running it. Alembic migration **0008_project_generality_report**.
- **DB**: unchanged. Both fixes read tables that already exist —
  `coding_asset_uses` (v1.15) and the indexed `coding_sessions.parent_session_id`
  (v1.14). No migration.

---

# API Contract v1.18 — The factory's own prompts as assets (FROZEN additions)

Additive on top of v1.17, and the first assets masterwork owns rather than
finds. The factory pipeline's stage prompts used to be Python string literals
inside the runner: the pipeline could be *run* but not *improved*. They now live
in a vendor-neutral role store on disk, and a third provider indexes them — so
edit, chat proposals, project links and simulations operate on the factory's own
agents with no new endpoint and no new schema.

## No new endpoints, no new schemas

Every existing asset endpoint (`listAssets`, `getAsset`, `updateAsset`,
`getAssetDiagram`, `generateAssetDiagram`, and everything that takes an asset id
— chat, proposals, projects, simulations, the usage rollups) accepts these ids
unchanged. The only OpenAPI diff is the `AssetSummary.provider` **description**,
which now names the third provider. **The generated client does not change.**

## The role store

```
~/.masterwork/agents/<role>/system.md    static identity        -> one asset
~/.masterwork/agents/<role>/user.md      per-turn task template -> one asset
~/.masterwork/agents/<role>/role.json    model / writes / purpose -> NOT an asset
```

Roles today are `plan`, `build`, `review`, `document`. The directory is created
by the factory on first run; a repo-local override (`<repo>/.masterwork/agents/`)
is out of scope here — this provider indexes the **global** store only.

## Behavior

- **Ids are `masterwork:agent:<role>:<part>`**, `<part>` ∈ `system` | `user` —
  e.g. `masterwork:agent:plan:system`. Three properties are load-bearing:
  - The provider segment is `masterwork`, so no id can collide with `claude:*`
    or `claude-plugin:*` however the store is named.
  - It round-trips through the id parser unchanged: that parser splits on the
    first two colons only (`maxsplit=2`), so `<role>:<part>` survives whole as
    the name, exactly like a plugin's `vercel:bootstrap`.
  - The kind is **`agent`**, not a new kind. The store *is* a directory of
    agents, and reusing the kind keeps roles inside the Agents list, the `kind`
    filter and the usage rollups instead of widening the `AssetKind` enum — a
    widening that would have made every one of these assets unroutable in the
    current client, whose id parser returns null for a kind that is not
    `skill`/`agent`.
- **A role name is validated, never mangled.** `[a-z0-9][a-z0-9_-]{0,63}`:
  lowercase (on a case-insensitive filesystem `Plan` and `plan` are one
  directory but would be two ids), and no colon (the id would stop
  round-tripping). A directory that fails it is skipped — it yields no assets
  and no error, and its files map to no asset id. Stray files at the top of the
  store, nested directories inside a role, and anything that is not
  `system.md`/`user.md` are likewise not assets.
- **One asset per editable file, not one per role.** `updateAsset` writes a whole
  file, and every writer in this app (chat proposals, simulation suggestions)
  emits prose. A role-as-one-asset would need a synthetic multi-file envelope
  that each of those writers would have to reproduce byte-exactly or corrupt
  both halves in one write. Two assets also match how the prompts actually fail:
  a vague identity is a `system.md` edit, a missing input is a `user.md` edit,
  and a simulation that scores one of them says which file to fix. Grouping the
  roster back together is what a **project** is for.
- **Writable, unlike plugin assets.** `read_only` is `false`, `PUT` writes the
  file, and the writable root is the whole store — so an accepted proposal can
  also create a role that does not exist yet (paths are still resolved through
  the same symlink/traversal check every provider uses; anything outside the
  store fails the accept with `path outside allowed roots`).
- **`role.json` is not an asset.** It is machine config, and one field of it —
  the `writes` boundary — is the security control that decides which files a
  headless agent may touch. The improvement machinery writes markdown with no
  schema validation anywhere in that path, so exposing the config to it means an
  LLM can emit prose into a JSON file (breaking the runner's config parse) or
  quietly widen the boundary of the agent it is editing. It is **read** instead:
  its `model` populates the asset's `model` field, and its `purpose` opens the
  asset's `description`, so the list UI shows what a role is for and what it
  runs on without either being editable here. Changing config stays a
  factory-side edit.
- **Derived title and description.** `title` is `"<role> · system prompt"` /
  `"<role> · task template"`; `description` is `purpose` (from `role.json`) plus
  what the file is, or just the latter when there is no config. Frontmatter is
  deliberately **not** parsed: these files are sent to the model verbatim, so a
  `---` block is prompt text, not metadata. Search covers them like any other
  asset — name, title, description and content — which means a role is findable
  by its purpose *and* by a `{{placeholder}}` in its template.
- **An absent store is zero assets, never an error.** Until the factory seeds it,
  `listAssets` simply contains no `masterwork` assets; `roots()` still reports
  the (missing) directory, so the first proposal that writes into it creates it.
- **DB**: none. Assets have never been in the database — the files are the source
  of truth. No migration.

## Role edits are snapshotted

These prompts *are* the pipeline, and the same improvement loop that edits them
can degrade them, so every write the API makes to the store is committed to git
first — the treatment `~/.claude` edits already got. **No endpoint, schema, or
response changes**: the snapshot is a disk side effect of `updateAsset`,
`acceptProposal` and `applySuggestion`, and history is read with `git`, not over
HTTP (there are no history/diff/revert endpoints for `~/.claude` assets either).

- **The repo is rooted at `~/.masterwork/agents`, not `~/.masterwork`.** The home
  also holds `masterwork.db` and `runs/` (append-only telemetry, unbounded).
  Rooting one level up and excluding them with an ignore file would make "the
  database is not in git" a rule that has to keep holding on every future write
  and every new subdirectory; rooting it at the store makes it unreachable —
  `git add -A` cannot see outside its own worktree.
- **Which tree records a write is the provider's answer**, not a constant: the
  role provider claims the store, the Claude provider claims `~/.claude`, the
  plugin provider claims nothing. A path in no tree is not snapshotted.
- **Masterwork creates the store's repo; it never creates `~/.claude`'s.** The
  store is masterwork's own directory, so the first write initializes it — git
  identity and `commit.gpgsign=false` set locally, since a fresh machine may
  have neither. `~/.claude` is the user's home: masterwork commits there when
  they made it a repo, and does nothing when they did not.
- **Anything already pending is committed as `masterwork: baseline snapshot`
  before the write.** The factory seeds and rewrites the store directly, and the
  user edits `~/.claude` by hand; `git add -A` would fold that into the commit
  that records the API write, producing a diff that shows two changes and a
  revert that undoes both. Baselining first keeps every recorded write a
  single-change commit. No-op when the tree is clean.
- **An absent store stays a no-op.** Nothing is created until the factory seeds
  it. A proposal that writes the very first role initializes the repo after the
  write, with the proposal's own message as the first commit — there is no
  earlier state to baseline.
- **Best-effort, as before**: a git failure is logged and swallowed. A write is
  never rejected because history could not be recorded.
- **`updateAsset` on a `~/.claude` asset is now snapshotted too** (previously
  only proposals and suggestions were). An unrecorded manual edit would also
  poison the next proposal's diff by folding both changes into one commit.

---

# API Contract v1.19 — Evidence: the envelope, and every gate check's note (FROZEN additions)

Additive on top of v1.18. A stage records `gates_passed` / `gates_failed` as
**counts**, and the envelope an agent returned is not stored at all — so the one
artefact that could improve an agent is the one thing masterwork throws away.
You cannot act on *3 failed*; you can act on *changed_files: claimed but not
changed on disk: README.md*, and on the envelope that made the claim. v1.19
stores both: one row per envelope **attempt**, and one row per gate **check**.

Unlike every other coding-observability table, these two are **reported, not
derived**. The producer states them on the hook body, and the hook body is not
part of the stored event stream — so a backfill preserves them instead of
rebuilding them, and recovers for older runs only what the stream still proves.

## New schemas

```
EnvelopeAttempt {                     // one envelope an agent returned
  id: number
  phase_id: number | null             // the stage it was returned in; join on it
  event_id: number | null             // the event that carried it
  role: string | null                 // plan | build | review | document
  attempt: number                     // 1-based, within (stage, role)
  parsed: boolean
  parse_error: string | null          // why it did not parse
  status: string | null               // the status it declared: ok | blocked | failed
  body: { [key: string]: any } | null // the envelope object, verbatim
  raw_text: string | null             // the reply it was read out of
  origin: string                      // reported | recovered — see below
  created_at: string
}

GateCheckItem {                       // one check a gate ran
  id: number
  phase_id: number | null
  event_id: number | null
  gate: string                        // envelope | artifacts | changed_files | boundary | …
  attempt: number                     // 1-based, within (stage, gate, item)
  item: string | null                 // the thing checked; null for a whole-gate verdict
  ok: boolean
  note: string | null                 // what the check wrote. The reason the row exists
  origin: string
  created_at: string
}
```

## Changed schemas

```
CodingSessionDetail += {
  envelopes: EnvelopeAttempt[]        // oldest first, capped at the most recent 100
  gate_checks: GateCheckItem[]        // oldest first, capped at the most recent 500
}

BackfillResult += { envelopes, gate_checks: number }
BackfillTotals += { envelopes, gate_checks: number }
```

`CodingSession` — the list shape — is **unchanged**: a card carries
`PhaseSummary[]`, not `CodingPhase[]`, and the two arrays hang off the detail
only. The grid never ships an envelope body.

## The ingest blocks

`POST /api/v1/hooks/events` gains two optional blocks, under the same
never-422 discipline as `phase` and `agent`: a producer that sends neither
behaves exactly as it did in v1.18.

```
envelope: {
  role:        string?   // the role that produced it; else the event's lane
  attempt:     number?   // 1-based; counted for you when omitted
  parsed:      boolean?  // defaults to `parse_error` being absent
  parse_error: string?   // why it did not parse
  status:      string?   // the status the envelope declared
  body:        object?   // the envelope object as returned
  raw_text:    string?   // the reply it was read out of
}

gate: {
  name:    string        // the gate that ran — required; a block without one records nothing
  attempt: number?       // 1-based; counted for you when omitted
  item:    string?       // default item for the checks below
  ok:      boolean?      // default verdict; else read off the event type
  note:    string?       // default note
  checks:  [ { item: string?, ok: boolean?, note: string? } ]?
}
```

- **`gate.checks` decides the row count.** Present → one row per entry, each
  entry falling back to the block's own `item`/`ok`/`note` for whatever it
  omits. Absent → the block *is* the one check, and `item` is null. This is what
  makes *one row per CHECK* possible for a gate like `checks`, which runs a
  command per row, without forcing a wrapper on a gate that is a single verdict.
- **`ok` falls back to the event type**: `gate_pass` → true, `gate_fail` →
  false. The runner already spells its verdict that way, so the minimal report
  is `gate: {"name": check.name, "note": check.note}` bolted onto the event it
  already sends. A block that omits `ok` on any other event type records
  nothing — a check with no verdict is not a check.
- **`envelope.parsed` defaults to `parse_error == null`**, which mirrors the
  runner's own `ParseResult.ok`. An envelope block carrying none of `body`,
  `raw_text`, `parse_error` and an explicit `parsed` records nothing: an attempt
  nobody can say anything about is not an attempt.
- **`attempt` is counted when omitted** — the number of rows already stored for
  the same (stage, role) or (stage, gate, item), plus one. A correction round
  therefore numbers itself, and a producer that knows better states it.
- **Both blocks are optional and independently droppable.** An unusable block is
  dropped **whole** by the same validator that guards `phase`/`agent`: never
  partially applied, never a 422. When a block is dropped the event still falls
  through to recovery, so `gate: {"name": "boundary", "checks": "not a list"}`
  on a `gate_fail` still records the boundary verdict from the payload.

Caps, all clipping rather than rejecting: `role` and `gate` 100 chars, `item`
500, `status` 20, `parse_error` 2 000, `note` 8 000, `raw_text` 32 KB, `body`
32 KB serialized under the existing payload capping. A clipped `note` or
`raw_text` gains a `… [truncated, N chars]` marker — a truncated reply that
looks complete is worse than no reply.

## Behavior

- **Sibling arrays, not nested inside the phase.** Both rows carry a nullable
  `phase_id` and the detail carries them flat, which is the join the rest of
  this contract already uses (`CodingEvent.phase_id` points at a stage the same
  way). Three reasons it beats nesting: evidence whose stage could not be
  resolved still reaches the reader instead of vanishing — the ingest never
  rejects, so a gate fired before any `phase_start` is a real case; *every
  failing check in this run* is a flat filter rather than a flatten-then-filter;
  and `CodingPhase` stays a row of scalars, which is what the waterfall wants.
  Grouping by `phase_id` is one line in the client.
- **`origin` says how much to trust the row.** `reported` — the producer sent
  it, and only these can carry an envelope `body`. `recovered` — a replay
  reconstructed it from a `gate_pass`/`gate_fail` line, so it says exactly what
  that line said and `body`/`raw_text`/`status` are always null. The UI must
  distinguish them: *no body recorded* is a fact about masterwork's history, not
  about the agent.
- **An event yields evidence from exactly one source.** A body that stated
  blocks is taken at its word and is never also mined; an event that stated
  nothing is mined. That is what stops one gate line becoming two rows.
- **A backfill preserves reported evidence and rebuilds only the recovered.**
  `POST /coding-sessions/{id}/backfill` deletes `origin = "recovered"` rows and
  replays; the reported ones survive, because the hook body they arrived on was
  never stored and no replay could recreate them. Since the replay drops and
  recreates every stage, a surviving row's `phase_id` would name a deleted
  stage — so it is **re-pointed** through `event_id`, the one handle that is
  stable across a rebuild. A stage the replay cannot rebuild (a producer that
  named it only on the body, never in `payload`) leaves the link honestly
  `null` rather than dangling. Still idempotent: replaying twice changes
  nothing.
- **What history recovers.** The pre-v1.19 stream carries a gate's verdict in
  the event type, its name in `payload.gate` and its note in `payload.detail`
  as `"<gate>: <note>"` — so **the gate, the pass/fail and the note come back in
  full**, with the redundant `"<gate>: "` prefix undone. The `envelope` gate's
  own line additionally proves that an envelope *attempt* happened and whether
  it parsed, so an `EnvelopeAttempt` is recovered for it — with `parse_error`
  when it failed, and `body`, `raw_text` and `status` **null**, because they
  were never posted and are not going to be guessed. `phase_end` carries the
  envelope's `summary_line`, which is a sentence about the envelope and not the
  envelope; it is left where it is, as the stage's `description`.
- **A verdict that named no gate is filed under the gate name `stage`.** The
  runner emits two such lines — an out-of-boundary revert, and a stage that
  returned a non-`ok` status — and both are stage-level judgements rather than
  one of the six named gates. Inventing a name for them would be a claim.
- **`gates_passed`/`gates_failed` are untouched.** The counters stay exactly
  what they were; these tables sit beside them. Nothing about the phase row
  changed.
- **DB**: new `coding_envelopes` and `coding_gate_checks` (autoincrement PK, FK
  → `coding_sessions` ON DELETE CASCADE, FK → `coding_phases` ON DELETE SET NULL
  so a rebuilt stage cannot delete the evidence, FK → `coding_events` ON DELETE
  CASCADE, and an index on `(session_id, phase_id)` plus one on `event_id`).
  Alembic migration **0017_coding_evidence**.
