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
