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

---

# API Contract v1.20 — Cross-run analytics, and child attribution (FROZEN additions)

Additive on top of v1.19. Every run reports its stages, its per-CHECK gate
evidence, its envelope attempts and its lanes — and every one of those numbers
was only ever readable **one run at a time**. There was no `GROUP BY` and no
`SUM` anywhere in the data layer, so *which gate keeps failing*, *which role
keeps being sent back*, *is it getting worse* and *was the expensive model worth
it* were all unanswerable from data that has been sitting in the database the
whole time. v1.20 is those four aggregates, plus the one attribution fix that
makes a pipeline's asset use visible at all.

## New endpoints

| Method & path | operation_id | Request | Response |
|---|---|---|---|
| GET `/api/v1/coding-analytics/gates?since=&workflow=&include_inspection=&include_children=` | `listGateStats` | the four shared filters | `GateStat[]` (failures desc, then checks desc, then gate) |
| GET `/api/v1/coding-analytics/roles?since=&workflow=&include_inspection=&include_children=` | `listRoleStats` | the four shared filters | `RoleStat[]` (corrections desc, then gate failures desc, then role) |
| GET `/api/v1/coding-analytics/runs?since=&workflow=&include_inspection=&include_children=&limit=` | `listRunStats` | the four, plus `limit` (1–500, default 100) | `RunStat[]` (**oldest first**) |
| GET `/api/v1/coding-analytics/models?since=&workflow=&include_inspection=&include_children=` | `listModelStats` | the four shared filters | `ModelStat[]` (runs desc, then model; the unnamed model last) |

All four carry the `coding` tag, so the generated client gains four methods on
the class it already has rather than a new one.

## New schemas

```
GateRoleStat {                        // one gate as one role experienced it
  role: string | null                 // the lane whose stage the check ran in
  checks: number                      // the rate's denominator
  failures: number
  failure_rate: number | null         // failures / checks
  runs: number                        // distinct runs the pair was seen in
}
GateFailureNote {
  note: string                        // verbatim; never normalized or clustered
  role: string | null
  occurrences: number
  last_seen_at: string
}
GateStat {
  gate: string                        // envelope | artifacts | changed_files | boundary | …
  checks, failures: number
  failure_rate: number | null
  runs: number
  by_role: GateRoleStat[]             // failure rate desc, then role
  top_failure_notes: GateFailureNote[] // commonest first, capped at 5 per gate
}

RoleStat {                            // one lane across every run
  role: string | null                 // plan | build | review | document | checks | git | main
  runs, stages: number
  corrections: number
  avg_corrections: number | null      // corrections / stages
  failed_stages: number
  stage_failure_rate: number | null
  timed_stages: number                // stages that reported a duration
  total_duration_ms: number
  avg_duration_ms: number | null
  costed_stages: number               // stages that reported a cost
  total_cost_usd: number
  avg_cost_usd: number | null
  tokens_in, tokens_out: number
  gate_checks, gate_failures: number  // from the STAGE COUNTERS — see below
  gate_failure_rate: number | null
  envelope_attempts: number
  envelope_failures: number           // attempts that did not parse
  envelope_failure_rate: number | null
}

RunStat {                             // one run as a point on a trend line
  session_id: string
  title: string | null                // derived exactly as the Sessions screen derives it
  workflow: string | null
  git_repo: string | null
  model: string | null
  status: string                      // the DERIVED status, not the stored one
  accepted: boolean                   // status == success
  started_at: string
  ended_at: string | null
  wall_ms, active_ms: number
  cost_usd: number | null
  tokens_total, tokens_in, tokens_out: number | null
  stages, corrections: number
  gates_passed, gates_failed: number  // from the stage counters
  gate_checks, gate_failures: number  // from the v1.19 evidence rows
  envelope_attempts, envelope_failures: number
  child_count: number
}

ModelStat {                           // one model, through the lanes it ran
  model: string | null                // null is a real row, not a gap
  lanes, runs, accepted_runs: number
  acceptance_rate: number | null
  stages, corrections: number
  avg_corrections: number | null
  failed_stages, timed_stages: number
  total_duration_ms: number
  avg_duration_ms: number | null
  cost_usd: number                    // summed over the LANES, not the stages
  tokens_in, tokens_out, turns: number
  gate_checks, gate_failures: number
  gate_failure_rate: number | null
}
```

## Changed schemas

```
AssetUse += { via_children: number }  // how many of `uses` came from a run this one launched
```

`AssetUse` is the entry in `CodingSession.assets` and `CodingSessionDetail.assets`,
so both the card and the detail gain the field. Nothing was removed and no
field changed type; the client regenerates and existing readers keep working.

## Behavior

- **The four shared filters mean the same thing on all four endpoints**, or the
  numbers would stop being comparable.
  - `include_inspection=false` (default) drops masterwork's own analysis runs —
    the ones launched with `~/.claude` as their working directory, which Read
    every linked asset's `SKILL.md`. This is the same exclusion `/coding-assets`
    applies and for the same reason: 14 of the first 22 recorded skill uses were
    masterwork inspecting assets rather than an agent using one. Getting it
    wrong does not make one number wrong, it makes every number a lie.
  - `include_children=false` (default) drops runs that another run launched. A
    pipeline's headless stage child is the *inside view* of a stage already
    counted on its parent: the stage's cost, its verdict and its corrections are
    reported on the parent, and the child additionally carries its own
    synthesized chat turns. Counting both puts the same work in twice and adds a
    `main` role that did the pipeline's work a second time. This is the exact
    complement of the asset roll-up below — between them, every use, stage and
    dollar is counted once.
  - `workflow` matches as it does on `listCodingSessions`: `"chat"` also matches
    the runs that never named a workflow, because nothing writes that value.
  - `since` follows v1.17's rule — **a window counts what happened inside it**,
    never the whole history of anything touched inside it. Each aggregate keys
    off the clock of the thing it counts, which is stated on each query
    parameter: a gate check by `created_at`, a role's figures by the stage's
    `started_at`, a run and a model by the run's `started_at`. A lane has no
    timestamp of its own, which is why the model comparison can only use the
    run's.
- **Every rate ships with its denominator, and an undefined rate is `null`.** A
  100 % failure rate over one check is noise; the client can only say so if it
  is handed the one. Nothing is hidden behind a server-side threshold — a
  minimum sample size is a display decision, and pushing it into the API would
  silently delete the only rows a small dataset has. Where a denominator is
  zero the rate is `null` rather than `0.0`: a role that ran no gate has an
  *unknown* failure rate, and `0.0` would read as *never fails*.
- **The two gate sources are different populations, on purpose.**
  `listRoleStats` and `listModelStats` read `gates_passed`/`gates_failed` off
  the stage rows, which every run ever recorded carries. `listGateStats` reads
  the v1.19 `coding_gate_checks` rows, which are the only place a gate's *name*
  and its *note* exist — and which a run recorded before v1.19 only has after
  `POST /coding-sessions/{id}/backfill` recovers what its stream still proves.
  The two can therefore disagree, and the one that covers more history is the
  role view. `RunStat` reports both side by side (`gates_failed` vs
  `gate_failures`) so the gap is visible per run rather than inferred.
- **Failure notes are grouped verbatim.** A gate's note usually names the files
  it is about (*claimed but not changed on disk: README.md*), so most counts are
  1 and the list reads as the most recent distinct failures rather than a
  ranking. Collapsing two sentences into one bucket would be a claim that they
  are the same failure, which masterwork has no basis for. Passing checks write
  notes too; only the failures are listed, because that is the actionable half.
- **`listRunStats` returns the most recent `limit` runs, oldest first.** A trend
  wants the latest runs, and reading them left to right is what makes a
  regression visible without the client re-sorting. `status` is the derived one
  (silence turns an unclosed run into `abandoned`), and `accepted` is exactly
  `status == success` — `abandoned` is silence, not a verdict, and counts as not
  accepted. `active_ms` is the same figure the Sessions screen shows, computed
  by the same function: a pipeline run prefers the measured sum of its stages.
- **A model's stages are a join, never a guess.** `coding_phases.agent` names a
  lane of the same run and that lane carries the model, so the stage's model is
  the model that ran it. Cost is summed over the *lanes* rather than the stages,
  because that is where a lane's cost is reported. The `model: null` row is
  kept, sorted last, and labelled — dropping it would hide runs rather than
  clean up the table — but it is **not a model**: it is every lane that named
  none, which is the pipeline's own `git` and `checks` lanes (they run no model
  and appear in every run) plus every agent lane recorded before the runner
  started sending one. A run therefore appears under it *and* under its real
  model, and its acceptance rate is close to the whole population's by
  construction. The field description says so.
- **A run's `assets` now include what the runs it launched used.** The pipeline
  runner's own process makes no tool calls at all — every skill and subagent is
  reached for inside a headless stage child — so a factory run's asset list was
  empty by construction, however many skills the pipeline actually used, and
  *which skills does the pipeline use* had no answer anywhere. The fold is
  **on by default and has no opt-out**: the value it replaces is always the
  empty list, an opt-out would default to a number that is always zero, and the
  list and the detail share the one serializer so they cannot disagree.
  - A child's uses arrive as a **laneless** row (`lane: null`): the child's lane
    is its own `main`, which is not one of the parent's lanes and would read as
    a lie. Rows still merge on `(kind, name, lane)`, so several children that
    used the same skill collapse into one row.
  - `via_children` keeps the fold legible — `uses - via_children` is what the
    run did on its own — so the roll-up is inspectable rather than silent.
  - **`/coding-assets` is unchanged and never folds.** The child is already a
    run of its own in that rollup, so folding there too would count one call
    twice. Between the fold here and `include_children=false` on the analytics,
    every recorded use is counted exactly once from every angle.
- **What was deliberately NOT built.** Percentile latencies (`p50`/`p95`) —
  Postgres has `percentile_cont` and SQLite does not, and the packaged default
  is SQLite; averages plus their denominators are what both dialects can say
  honestly. Time-bucketed series (per day, per week) — `date_trunc` is
  Postgres-only, and `listRunStats` hands the client the raw per-run points to
  bucket however it likes. Note clustering — see above.
- **DB**: unchanged. Every aggregate reads tables that already exist
  (`coding_sessions`, `coding_phases`, `coding_agents`, `coding_assets`,
  `coding_envelopes`, `coding_gate_checks`) through their existing indexes. No
  migration.

---

# API Contract v1.21 — Stated provenance, and when an asset was written (FROZEN additions)

Additive on top of v1.20. Two unrelated corrections that share one theme: a
number the app was confident about and had no right to be.

A pipeline's stage children were attached to their run by *inference* — a regex
for `factory/run.py` in the launcher's process ancestry, then the run that owned
that working directory at that instant. Invoke the runner from inside `factory/`
and its argv reads `run.py`, the regex matches nothing, no child is linked, and
the parent run then reports **no skills used** while its children happily loaded
three. Nothing failed, nothing was logged, and the answer on the screen was
wrong. That is the exact category of failure this app exists to eliminate, so
the runner now *states* what it launched and the inference is demoted to a
fallback for sessions recorded before it did.

Separately, assets exposed `updated_at` and no creation date, so a skill written
in July and a skill written yesterday were indistinguishable in a list sorted by
the only date there was.

## Changed schemas

```
AssetSummary {                        // and AssetDetail, which extends it
  …unchanged…
+ created_at: string | null           // ISO-8601; null where the platform records none
}
```

`created_at` is **nullable and always present**. Every existing field is
untouched, so the generated client gains one optional property.

## The stage-child signal (the frozen half of the runner contract)

The runner exports two variables into the environment of each `claude -p` stage
child:

| Environment variable | Meaning |
|---|---|
| `MASTERWORK_FACTORY_RUN_ID` | the run's id — **not** the session id |
| `MASTERWORK_FACTORY_STAGE` | the stage's name, e.g. `build` |

The Claude Code forwarder copies them into the **`SessionStart` event's
`payload`**, under exactly these keys:

```
POST /api/v1/hooks/events
{
  "session_id": "…",
  "event_type": "SessionStart",
  "payload": {
    "factory_run_id": "abc123",       // MASTERWORK_FACTORY_RUN_ID, trimmed, ≤200 chars
    "factory_stage": "build",         // MASTERWORK_FACTORY_STAGE, trimmed, ≤100 chars
    "launched_by": [ … ],             // unchanged
    "source": "…", …
  }
}
```

`payload` is already free-form on `HookEventRequest`, so **no request schema
changed** and no client regeneration is needed for this half.

## Behavior

- **Explicit beats inferred, always.** A `factory_run_id` in the payload
  resolves to the parent session id `factory-<run_id>` — the same string
  `telemetry.py` builds for the runner's own session — and that is the parent.
  The cwd, the launcher's argv and the time window are not consulted, so there
  is nothing about *how* the runner was invoked that can break the link. Where
  the stated parent and the inferred one disagree, the stated one wins: it is a
  statement by the process that did the launching, and the other is a guess
  about a command line.
- **The stage name comes from the runner, not from a `phase_at` lookup.** The
  title is rendered the same as before — `"<stage> stage · factory-<run_id>"`,
  `title_source: "provenance"` — so nothing downstream re-learns a format. A
  child that carried a run id but no stage name is titled `"stage · <parent>"`
  and still linked: a missing stage name costs a title, not the attachment.
- **The signal rides `SessionStart` only.** Repeating it on every tool call
  would multiply the stream to say the same thing once per event.
- **A stated run that was never recorded does not become a parent.** Pointing
  `parent_session_id` at a session id that does not exist would remove the run
  from the grid (`roots_only` is `parent_session_id IS NULL`) *without* filing
  it under anything — invisible is worse than orphaned. The child stays a root,
  and because the signal is stored on the event, `POST
  /coding-sessions/{id}/backfill` links it once the run is recorded. This is
  also what makes an out-of-order start repairable rather than permanent.
- **A stated child is `automated` whatever its ancestry looks like.** Every
  stage is spawned as `claude -p`; the runner saying so outranks matching
  `_HEADLESS_LAUNCH` against whatever the process tree happens to read as.
- **The regex path still works, and is now explicitly the legacy one.** Every
  session recorded before this change carries a `launched_by` chain and no
  stated signal, and links exactly as it did in v1.13. Backfill replays reach
  the same verdict as a live ingest either way, because both read the stored
  payload.
- **A forwarder upgrade is now detectable.** `GET
  /observability/integrations` compared the *hook command strings* and the
  *existence* of the installed script, and a command string names a path that
  does not change when the script behind it does. An install shipping a new
  forwarder therefore read as `connected` while the hook on disk kept sending
  the old body — the backend waiting for a field that would never arrive, with
  no screen saying so. The status now also compares the installed copy's
  **bytes** against the script this install ships, and reports `outdated` when
  they differ; `connect` overwrites it as it always did. Byte equality rather
  than a version constant, because a version constant is one forgotten bump away
  from reintroducing exactly this bug.
- **`created_at` is the filesystem birth time, and null where there is none.**
  macOS and the BSDs record `st_birthtime`; Linux's stat carries no birth time
  at all. The tempting fallback is the mtime, and it is a lie in precisely the
  case the field was added for — a skill written in July and edited yesterday
  would report *created yesterday*, answering the user's question with the
  wrong date rather than admitting it cannot. Null says "this platform does not
  know", which a client can render as a dash.
- **`created_at` is never later than `updated_at`.** A copy, a restore or a
  fresh checkout gives an old file a birth time of today while its content is
  provably older, and *created after modified* is false on its face. The earlier
  of the two is reported.
- **Every provider carries it**, including the read-only plugin provider and the
  factory's own role store — one `stat` that each of them was already making.
- **DB**: unchanged. No migration. Assets have never been stored in the
  database, and the stage signal is written to columns that already exist
  (`coding_sessions.parent_session_id`, `.title`, `.title_source`,
  `.launch_mode`).

---

# API Contract v1.22 — Images in event payloads (FROZEN additions)

Additive on top of v1.21. A screenshot tool — `mcp__Claude_Browser__computer`,
the iOS simulator's `control`, `Read` on a PNG — answers with the image inline
as base64. That answer is hundreds of KB, so the forwarder's `compact()` cut it
to a 2 000-character prefix of a JPEG: undecodable, unreadable, and the only
trace of the picture in the whole system. The bytes now leave the payload before
any cap sees them, and the event carries a reference the UI can point an `<img>`
at.

## New payload shape

Inside `CodingEvent.payload.tool_input` / `.tool_response`, at any depth, an
inline image block is replaced by:

```
{ type: "image_ref", media_id: string, media_type: string, bytes: number }
```

`media_id` is `<sha256-of-the-bytes>.<jpg|png|gif|webp>`. When the image could
not be written (undecodable base64, over 12 MB, an unwritable directory) the
block becomes `{ type: "image_omitted", media_type: string }` — the fact
survives, the bytes do not. An image the tool referenced *by URL* is left
untouched: there are no bytes to extract, and the reference is already small.

## New endpoint

| Method & path | operation_id | Request | Response |
|---|---|---|---|
| GET `/api/v1/coding-sessions/{session_id}/media/{media_id}` | `getCodingSessionMedia` | — | the image bytes, `Content-Type: image/*` (404 unknown/ill-formed id) |

## Behavior

- **The extraction happens in the hook, not the backend.** Every cap downstream
  — the forwarder's 4 000/2 000, ingest's 32 768, the UI's 4 000 — sits *after*
  the point where a base64 screenshot has already blown the budget. The only
  place that ever holds the whole image is the hook process, so that is where it
  is written.
- **Content-addressed.** The file name is the sha256 of the bytes, so the same
  screenshot taken twice is one file, a replayed session costs nothing, and the
  URL can be served `immutable` — what a hash names can never change.
- **Never in the database.** Images are files under
  `<masterwork home>/media/<session_id>/`, the only bytes in this app outside
  Postgres. Two consequences, both deliberate: a cleaned media directory leaves
  events pointing at images that are gone (the endpoint answers 404, the UI
  shows a broken thumbnail rather than losing the row), and the directory grows
  until someone deletes it.
- **Both halves of the media path are matched, not sanitised.** `session_id`
  must be `[A-Za-z0-9_-]{1,64}` and `media_id` must be a 64-hex name with a
  known image extension. Anything else is a 404 before any filesystem call. The
  forwarder applies the same rule when it *writes*, so a hostile session id
  cannot climb out of the media root.
- **Only PostToolUse carries images**, and only for the four raster types above.
  `PreToolUse` fires for subagent spawns alone, whose input is text.
- **The sidecar now names the media directory.** `connect()` writes
  `{ingest_url, media_dir}` to `~/.masterwork/hooks/config.json`, so relocating
  masterwork's home moves both destinations together. An older sidecar without
  the key falls back to `~/.masterwork/media`; `MASTERWORK_MEDIA_DIR` overrides
  both.
- **Existing events are unaffected and unrecoverable.** Their images were
  truncated at capture time; nothing in the database can be replayed into a
  picture. The feature starts at the next `connect()`, which is also what
  installs the new forwarder.
- **DB**: unchanged. No migration.

# API Contract v1.23 — A run's title is a summary, not its prompt (FROZEN additions)

Additive on top of v1.22. A card titled with the first 300 characters of a
prompt shows the opening of a message, not what the run was about — and a
prompt that opens with context ("So I was looking at the sessions screen and…")
buries the request under the throat-clearing. The agent that received the prompt
already understood it, so it names the run itself; the prompt stays, one screen
down, where it can be checked against the name.

## Changed schemas

```
CodingSession.title_source            // now also "summary"
```

## The title marker (the frozen half of the agent contract)

An agent names its run by echoing a marker, the same channel the factory-or-chat
router already uses for its verdict:

```bash
echo "masterwork:title=Five to fifteen word summary of what I was asked"
```

Read off `payload.tool_input.command` of any tool event — `echo` exists on every
machine, and a shell command is the one thing a hook payload carries verbatim.
Matched on `masterwork:title=` with a non-empty value, so the sentence that
*documents* the marker (in a skill file, in a `grep`) is not a title; the value
ends at the first quote or newline and is stored at 120 characters.

## Behavior

- **`summary` outranks `prompt`, and nothing else.** The rank order is
  `factory` > `provenance` > `summary` > `prompt`. A summary is a better name
  for the same request the prompt states, so it replaces it — but a pipeline
  stage child keeps the provenance name that puts it under its parent, because
  a stage prompted with boilerplate would otherwise summarise the boilerplate.
- **The first title wins.** Equal-ranked titles never replace (unchanged rule),
  so a session that routes a second task keeps the name of the first — the same
  way it keeps the first prompt rather than the fifth.
- **Untitled runs are unaffected.** No marker means the prompt still titles the
  run, and a prompt-less run still falls back to `cwd`. Nothing is required of
  any producer, and no stored row changes.
- **The prompt moved, it did not go away.** It is where it always was, in the
  first `UserPromptSubmit` event's payload; the session detail reads it from
  there (first three lines, expandable) rather than from `title`. Prompts that
  are envelopes — `<task-notification>`, `<system-reminder>` — are skipped when
  looking for the request, so a run resumed by a background task before its
  human typed still shows the human's words.
- **Images in a prompt are still not captured.** The `UserPromptSubmit` hook
  sends `prompt` as a string; a pasted image is a placeholder in it, and the
  bytes only exist in the transcript. The request block renders `image_ref`
  nodes if it ever finds any, so this becomes a forwarder change alone.
- **DB**: unchanged. No migration — `title_source` is already a free string.

---

# API Contract v1.24 — Azure DevOps work items, read-only inbound (FROZEN additions)

Additive on top of v1.23. Masterwork can now mirror the Azure DevOps work items
assigned to the user. This is **read-only inbound only**: the DevOps client
(`app/providers/azuredevops.py`) has no PATCH/PUT/DELETE/comment-post method at
all, and outbound stays a deliberate stub
(`app/services/work_outbound.perform` always raises `NotImplementedError`).
The PAT is never stored — `WorkSource.secret_ref` only names the environment
variable it is read from at call time.

## New schemas

```
WorkSource {
  id: string                    // uuid
  provider: string               // "azuredevops" today
  org_url: string
  project: string
  team: string | null
  query_wiql: string | null      // overrides the default assigned-to-me WIQL when set
  secret_ref: string             // env var naming the PAT — never the PAT itself
  current_iteration: string | null // the team's current sprint path, refreshed on sync
  last_sync_at: string | null
  created_at: string
  updated_at: string
}
WorkSourceCreateRequest {
  org_url: string                // must match ^https://dev\.azure\.com/[A-Za-z0-9._~-]+/?$
  project: string
  team?: string | null
  query_wiql?: string | null
  secret_ref?: string            // default "AZURE_DEVOPS_PAT"
}
WorkItem {
  id: number
  source_id: string
  external_id: number            // DevOps work item id
  external_url: string           // {org_url}/{project}/_workitems/edit/{external_id}
  item_type: string              // System.WorkItemType, e.g. "Bug"
  title: string
  description_md: string         // System.Description, HTML converted to markdown
  acceptance_md: string | null   // Microsoft.VSTS.Common.AcceptanceCriteria, converted; null if absent
  state: string
  iteration: string | null
  assigned_to: string | null     // System.AssignedTo display name
  priority: number | null
  tags: string[] | null
  external_changed_at: string
  synced_at: string
}
WorkSyncResult { fetched: number, inserted: number, updated: number }
WorkItemStartResponse {
  prompt: string                 // the assembled session prompt
  launched: boolean               // always false today — see Behavior
  session_id: string | null      // null until a launched session is linked
  link_id: number                // the work_item_sessions row id
}
```

## New endpoints

| Method & path | operation_id | Request | Response |
|---|---|---|---|
| GET `/api/v1/work/sources` | `listWorkSources` | — | `WorkSource[]` (created_at desc) |
| POST `/api/v1/work/sources` | `createWorkSource` | `WorkSourceCreateRequest` | `WorkSource` (201; 400 `InvalidWorkSourceError` on a non-DevOps `org_url`) |
| GET `/api/v1/work/items?source_id=&state=` | `listWorkItems` | query: both optional | `WorkItem[]` |
| POST `/api/v1/work/sources/{source_id}/sync` | `syncWorkSource` | — | `WorkSyncResult` (404 unknown source; 502 `WorkSyncError` on a DevOps failure or an unset PAT) |
| POST `/api/v1/work/items/{item_id}/start` | `startWorkItem` | — | `WorkItemStartResponse` (404 unknown item) |

## Behavior

- **Sync**: runs `source.query_wiql`, or the default
  `SELECT [System.Id] FROM WorkItems WHERE [System.AssignedTo] = @Me AND
  [System.State] NOT IN ('Closed','Removed','Done') ORDER BY
  [System.ChangedDate] DESC` when unset, against the DevOps WIQL endpoint;
  batch-fetches the returned ids in chunks of 200
  (`Microsoft.VSTS.Common.AcceptanceCriteria`, `System.Description`, and seven
  other fields); converts `System.Description` and
  `Microsoft.VSTS.Common.AcceptanceCriteria` from HTML to markdown with
  `markdownify`; upserts one `work_items` row per `(source_id, external_id)`
  (select-then-insert-or-update, not a dialect-specific `ON CONFLICT`); stamps
  `work_sources.last_sync_at`. The whole DevOps payload is stored unchanged in
  `raw` — untrusted external data, rendered as markdown, never executed.
- **`startWorkItem` still only assembles a prompt, it does not launch.** A
  reusable launch path now exists (`POST /api/v1/launcher/launch`, see the
  Session launcher section below), but rewiring this endpoint to call it is
  out of scope here — `startWorkItem` assembles the prompt (title line,
  `external_url`, `## Story` + `description_md`, then `## Acceptance criteria`
  + `acceptance_md` — that section omitted entirely when acceptance criteria
  is absent or empty) and returns it with `launched: false`. It writes a
  `work_item_sessions` row (`kind: "spawned"`, `session_id: null`) so the
  request is on record; nothing in v1 ever fills in that `session_id`.
- **No write path exists.** `AzureDevOpsClient` (in `app/providers/`, not a
  `Provider` — it is not registered in `build_providers`) exposes only
  `query_work_item_ids`, `get_work_items_batch`, `list_active_prs`, and
  `list_pr_threads`; the last two are implemented and unit-tested but unused
  by any endpoint in v1. `work_outbound.perform` describes a
  `ProposedOutboundAction` (state change / comment / PR link) and always
  raises before doing anything.
- **DB**: three new tables — `work_sources`, `work_items` (unique on
  `(source_id, external_id)`, indexed on `(source_id, state)`),
  `work_item_sessions` (`session_id` nullable, FK `coding_sessions.id` ON
  DELETE CASCADE). Alembic migration `0018_work_items`.

---

# API Contract v1.25 — in-app session launcher (FROZEN additions)

Additive on top of v1.24. A coding session can now be started from the
Sessions screen, always through `factory/run.py` — never a bare `claude`
invocation. `projects_root` (default `~/Projects`, expanded server-side) is a
persisted setting; every path this feature touches is resolved and checked to
live inside it with `resolve_within_roots` (`app/providers/base.py`), the same
helper the asset write path uses.

## New schemas

```
AppSettings { projects_root: string }               // absolute, expanded ~
AppSettingsUpdateRequest { projects_root?: string | null }   // omitted/null = unchanged

LaunchMode = "autonomous" | "interview"

LauncherProject {
  name: string
  path: string           // absolute, under projects_root
  is_git_repo: boolean
}
LauncherProjectCreateRequest { name: string }   // no path separators or traversal

LaunchRequest {
  project_path: string   // absolute; must resolve under projects_root
  request_text: string   // min length 1
  mode?: LaunchMode       // default "autonomous"
}
SessionLaunchRead {
  id: number
  project_path: string
  request_text: string
  mode: LaunchMode
  launched_at: string
  pid: number | null
  launched: boolean       // true once the subprocess was spawned
}
```

## New endpoints

| Method & path | operation_id | Request | Response |
|---|---|---|---|
| GET `/api/v1/settings` | `getSettings` | — | `AppSettings` (default filled in when unset) |
| PATCH `/api/v1/settings` | `updateSettings` | `AppSettingsUpdateRequest` | `AppSettings` (400 on a relative or non-existent `projects_root`) |
| GET `/api/v1/launcher/projects` | `listLauncherProjects` | — | `LauncherProject[]` (immediate subdirectories of `projects_root`, sorted, dotted dirs and non-directories excluded) |
| POST `/api/v1/launcher/projects` | `createLauncherProject` | `LauncherProjectCreateRequest` | `LauncherProject` (201; `mkdir` + `git init`; 400 on an invalid name; 409 if it already exists) |
| POST `/api/v1/launcher/launch` | `launchSession` | `LaunchRequest` | `SessionLaunchRead` (400 when `project_path` is outside `projects_root`, not a directory, or not a git repo; 502 `LaunchFailedError` if the spawn itself fails) |

## Behavior

- **Always the factory, never a bare `claude` call.** The launch endpoint
  spawns `python3 <masterwork_repo_root>/factory/run.py --repo <project_path>
  "<request_text>"` as a detached, fire-and-forget `Popen`
  (`start_new_session=True`, argv list, never `shell=True` — `request_text` is
  untrusted and is passed as a single argv element). Nothing waits on it; a
  `session_launches` row is written first (so a launch is on record even if
  the spawn fails) and stamped with the child's pid.
- **`mode` selects the spawned argv.** `"autonomous"` is unchanged: no mode
  flag, no run id. `"interview"` gets a server-generated run id passed as
  `--run-id`, plus `--interview` — see v1.26 below for what that does.
- **No extra attribution wiring.** A launched run's Claude sessions
  self-attribute to the Sessions screen the same way every other factory run
  does, via the `MASTERWORK_FACTORY_RUN_ID` env handshake
  (`factory/adw/agent.py`, forwarded by
  `app/observability/forwarders/claude_code.py`) — nothing in
  `app/api/v1/coding/` changes for this feature.
- **A run that dies at startup is not surfaced beyond its log.**
  `factory/run.py` exits 2 for a missing/non-git repo or an unresolvable
  config; the launch endpoint pre-checks directory-ness and `.git` so those
  cases 400 synchronously instead, but a failure after that 200 is only
  visible in `~/.masterwork/launches/<launch id>.log` — the response never
  claims the run succeeded, only that it started.
- **Project name validation** (`POST /api/v1/launcher/projects`) rejects
  empty/whitespace-only names, `/`, `\`, and NUL bytes, `.` and `..`, any name
  starting with `.`, and names over 100 characters.
- **DB**: two new tables — `app_settings` (`key` PK, key-value so a future
  setting needs no migration) and `session_launches` (`id`, `project_path`,
  `request_text`, `mode`, `launched_at`, `pid` nullable). Not linked to
  `coding_sessions` by FK — attribution rides the env handshake, not this
  table. Alembic migration `0020_app_settings_and_launches`.

---

# API Contract v1.26 — real interview mode

Additive on top of v1.25. An interview launch now really pauses: the factory
run executes `plan` as always, then — instead of continuing to `build` — turns
the plan envelope's `assumptions[]` into questions, writes them to
`<run_dir>/questions.json`, marks `run.json` `state: "waiting_input"`, and
exits 0. `--resume <run_id>` on such a run requires `<run_dir>/answers.json`;
with it, each question+answer pair is folded into the build stage's prompt
alongside the plan; without it, the resume refuses and exits 2. An autonomous
launch, or any run started without `--interview`, is unaffected.

## Factory CLI (`factory/run.py`)

- `--interview` — after `plan`, pause and write `questions.json` instead of
  continuing to build. Refused (exit 2) on a workflow with no `plan` stage.
- `--run-id RUN_ID` — use this id for a fresh run instead of generating one.
  Validated as a path segment before use: non-empty, ≤64 chars,
  `[A-Za-z0-9._-]` only, not `.`/`..`, and refused if already in use under the
  runs root. Mutually exclusive with `--resume` (a resume takes its id from
  the record it is resuming).
- `--resume <run_id>` of a run whose `run.json` state is `waiting_input` reads
  `<run_dir>/answers.json`; missing or malformed answers refuse the resume
  (exit 2) before any agent runs.

## The on-disk file contract (owned by `factory/adw/interview.py`)

```
<run_dir>/questions.json   — written by the factory
{
  "run_id": "a1b2c3d4",
  "stage": "plan",
  "asked_at": "2026-08-14T10:00:00+00:00",
  "questions": [{ "id": "q1", "question": "<assumption text, verbatim>" }]
}

<run_dir>/answers.json     — written by the backend, read by the factory
{
  "answered_at": "2026-08-14T10:05:00+00:00",
  "answers": [{ "id": "q1", "question": "<verbatim>", "answer": "<user text>" }]
}
```

`<run_dir>` is `<runs root>/<run_id>`; the runs root is a repo's own
`factory.config.json` `"runs_dir"` when set, else
`~/.masterwork/runs/<project dir name>`. The backend
(`app/services/factory_runs.py`) mirrors this rule exactly rather than passing
a `--runs-dir`, so an interview run's files land where every other run of that
repo lands. Both files are written atomically (tmp + `os.replace`).

## New schemas

```
SessionLaunchRead: + run_id: string | null        // set for interview launches only

InterviewState = "not_interview" | "starting" | "running" | "waiting" | "answered" | "finished"
InterviewQuestion { id: string, question: string }
InterviewRead {
  launch_id: number
  run_id: string | null
  state: InterviewState
  run_state?: string | null    // run.json's raw state, for debugging
  questions?: InterviewQuestion[]   // only populated when state == "waiting"
}
InterviewAnswer { id: string, answer: string }        // min length 1
InterviewAnswersRequest { answers: InterviewAnswer[] }  // min length 1, one per question
InterviewResumeRead { launch_id: number, run_id: string, resumed: boolean, pid: number | null }

SessionLaunchListItem = SessionLaunchRead + { interview: InterviewRead | null }  // null for autonomous rows
```

## New endpoints

| Method & path | operation_id | Request | Response |
|---|---|---|---|
| GET `/api/v1/launcher/launches` | `listSessionLaunches` | — | `SessionLaunchListItem[]` (newest first, up to 20) |
| GET `/api/v1/launcher/launches/{launch_id}/interview` | `getLaunchInterview` | — | `InterviewRead` (404 unknown launch) |
| POST `/api/v1/launcher/launches/{launch_id}/answers` | `submitInterviewAnswers` | `InterviewAnswersRequest` | `InterviewResumeRead` (404 unknown launch; 409 not currently `"waiting"`; 400 answer ids don't match the recorded questions one-for-one, or any answer is blank after `.strip()`) |

## Behavior

- **Interview launches get a server-generated run id.** `POST
  /api/v1/launcher/launch` with `mode: "interview"` generates a run id
  (`factory_runs.new_run_id()`, same shape as the factory's own), stores it on
  the `session_launches` row, and passes `--run-id <id> --interview` to
  `run.py`. An autonomous launch keeps `run_id: null` and its argv
  byte-for-byte identical to v1.25.
- **State is derived from the run's own files, not stored.** `starting` (no
  `run.json` yet) → `waiting` (`questions.json` present, `run.json` state
  `waiting_input`) → `answered` (`answers.json` written) → `finished`
  (`run.json` state `finished`/`stopped`), or `running` otherwise. A launch
  that isn't interview mode, or has no run id, reads `not_interview`.
- **Submitting answers is the double-submit guard.** Once `answers.json`
  exists the state is `answered`, not `waiting`, so a second POST 409s instead
  of spawning a second resume. The answers file is written *before* the resume
  is spawned — a resume that started before its answers existed would refuse
  itself.
- **The resume spawn mirrors the launch spawn.** Same detached, fire-and-forget
  `Popen` pattern (`start_new_session=True`, argv list, never shell-interpreted):
  `python3 <repo>/factory/run.py --repo <project> --resume <run_id>`, through
  an injected `ResumeSpawner` dependency so no test forks a real process.
- **Frontend**: the Sessions list page polls `listSessionLaunches` (5s) and
  renders an `InterviewQuestions` card — one required text field per pending
  question — for every launch currently `waiting`, mounted outside the Radix
  tabs so it is never hidden behind whichever tab is open.
- **DB**: one nullable column, `session_launches.run_id` (`String(64)`).
  Alembic migration `0022_session_launch_run_id` on `0021_work_assignee_sprint`.
  Additive, reversible, no backfill.

# API Contract v1.27 — server-side folder picker

Additive on top of v1.26. The projects root can now be set by browsing the
filesystem instead of typing a path: a new read-only endpoint lists a
directory's subfolders, and the launch dialog's **Browse** panel walks it and
saves the chosen folder through the existing settings PATCH.

## New schemas

```
DirectoryEntry { name: string, path: string }   // path is absolute
DirectoryListing {
  path: string                  // the resolved directory this listing is for
  parent?: string | null        // absolute path of the parent; null at the filesystem root
  entries?: DirectoryEntry[]    // non-hidden subdirectories, sorted by name
}
```

## New endpoint

| Method & path | operation_id | Request | Response |
|---|---|---|---|
| GET `/api/v1/launcher/browse` | `browseDirectories` | `path` query param, optional absolute path | `DirectoryListing` (400 relative/nonexistent/not-a-directory/unreadable `path`) |

## Behavior

- **Browsing is deliberately unrestricted.** Unlike `project_path` on
  `POST /api/v1/launcher/launch`, `browse` does not confine `path` to
  `projects_root` via `resolve_within_roots` — the picker exists to let the
  user *leave* the current root, and `PATCH /api/v1/settings` already accepts
  any absolute path. This is a local single-user app; no file contents are
  ever returned, only directory names.
- **Defaults to the stored `projects_root`.** Omitting `path` browses the same
  directory `GET /api/v1/launcher/projects` lists from. If that root has since
  vanished, the response is an empty `entries` list rather than a 400 — the
  same posture `list_projects` already takes — since the request wasn't
  parameterized by the caller.
- **Directories only, hidden entries excluded.** Files are never returned;
  entries whose name starts with `.` are skipped, matching `list_projects`.
- **Two-layer permission handling.** A `path` this process cannot list at all
  (`PermissionError` from `iterdir`) 400s — there's nothing to show. An
  individual entry that raises `PermissionError` when stat'd is silently
  skipped so one unreadable subfolder doesn't fail the whole listing.
- **`parent` is `null` only at the filesystem root** (`path.parent == path`).
- **Nothing is written by this endpoint.** The only write in the picker flow
  is the existing `PATCH /api/v1/settings`, which already invalidates both the
  settings and launcher-projects queries on success.
- **DB**: none. **Migrations**: none — the alembic head stays
  `0022_session_launch_run_id`.

# API Contract v1.28 — folder picker home shortcut

Additive on top of v1.27. `DirectoryListing` gains one required field so the
picker can offer a Home shortcut that points at the *backend's* home directory
— the browser has no way to learn it, and on a remote backend the browser's own
home would be the wrong machine's.

## Changed schema

```
DirectoryListing {
  path: string                  // unchanged
  parent?: string | null        // unchanged
  entries?: DirectoryEntry[]    // unchanged
  home: string                  // NEW, required — absolute path of the home
                                //      directory the backend process runs as
}
```

## Behavior

- **`home` is `Path.home()` of the backend process**, resolved per request. It
  is a navigation hint only: `browse` neither defaults to it nor treats it as a
  boundary, and passing it back as `path` is an ordinary browse.
- **Required, not optional.** Every `browse` response carries it, so the client
  never has to guess a home directory from path segments.
- **DB**: none. **Migrations**: none — the alembic head stays
  `0022_session_launch_run_id`.

# API Contract v1.29 — context-growth series

Additive on top of v1.28. Claude Code's transcript already carries one
cumulative `usage` block per API response — every assistant turn says how big
the context window was at that instant — and the forwarder used to throw all
of it away except the final sum. It now keeps the shape of that curve: an
ordered sample per turn, posted on the existing Stop/SessionEnd hook body,
stored in a new reported table, and read back as a session detail panel.

## New schemas

```
ContextSampleIn {                 // the hook's inbound shape, one per sample
  seq: int                        // position in the deduped stream, chronological across lanes
  message_id: string
  at?: datetime | null            // falls back to the event's own time when absent
  total_tokens: int                // input + cache_read + cache_creation
  output_tokens?: int | null
  model?: string | null           // accepted, not stored — no column for it
  is_sidechain?: bool = false
  tools?: string[] | null         // tool results that landed since the previous sample
}

ContextSample {                   // the read-back shape
  seq: int
  message_id: string
  at: datetime
  total_tokens: int
  output_tokens: int | null
  delta_tokens: int | null        // null for the first sample of its lane
  is_truncation: bool             // derived: delta_tokens is not null and negative
  tools: string[]
}

ContextToolCost {
  tool: string
  delta_tokens: int               // summed POSITIVE delta attributed to it
  calls: int                      // samples this tool appeared in
}

ContextSeries {
  session_id: string
  baseline_tokens: int | null     // first main-lane sample's total — static preamble + first prompt
  peak_tokens: int | null         // highest main-lane total reached
  samples: ContextSample[]        // main lane, ordered by seq
  sidechain_samples: ContextSample[]  // subagent turns, their own series
  tools: ContextToolCost[]        // main-lane roll-up, summed delta descending then tool name
}
```

`HookEventRequest` gains one optional field: `context_samples: ContextSampleIn[] | null`.

## New endpoint

| Method & path | operation_id | Request | Response |
|---|---|---|---|
| GET `/api/v1/coding-sessions/{session_id}/context` | `readSessionContextSeries` | — | `ContextSeries` (404 unknown session; 200 with empty arrays and null baseline/peak for a session with no samples) |

## Behavior

- **The forwarder walks the same transcript `transcript_usage()` reads**, and
  dedupes by assistant message id the same way — one API response spans
  several transcript lines carrying the same cumulative usage. The trap:
  dedupe gates *sample creation* only, never `tool_use` collection, which runs
  over every assistant line regardless — a tool named on the line after the
  first (same message id) would otherwise be lost. `tools[]` on a sample is
  resolved by `tool_use_id` against those blocks; an id that resolves to
  nothing is dropped, never invented.
- **Lanes never interleave.** `is_sidechain` is read straight off the
  transcript's `isSidechain` flag, and the pending-tools accumulator is keyed
  by it, so a subagent's `tool_result` can never land on the main lane's next
  sample. `seq` stays one monotonic counter chronological across both lanes;
  the read side is what splits them apart.
  Bounded like everything else the hook posts: the last 2000 samples, 20 tool
  names per sample.
- **`coding_context_samples` is REPORTED, not derived** — exactly like
  `coding_envelopes` and `coding_gate_checks`. The hook body carrying the
  series is never stored, so `service.backfill_session` leaves this table
  alone rather than clearing it. Ingest upserts by `(session_id, message_id)`
  in `_apply` only — never `_apply_derived`, which a backfill replays — and
  recomputes `delta_tokens` for the whole session, per lane, in one pass on
  every ingest. Recomputing wholesale rather than incrementally is what makes
  a re-post of the same cumulative list idempotent.
- **Negative deltas are real and stored verbatim.** A context truncation drops
  the total mid-session — observed as a −7570-token step in a live transcript.
  Such a sample is flagged `is_truncation` and excluded from the tool
  roll-up: a truncation is not something a tool earned.
- **The roll-up splits a multi-tool sample's delta exactly**: `delta //
  len(tools)` to each, the remainder to the first tools named, so the parts
  sum back to the delta. Only samples with a positive `delta_tokens` and at
  least one named tool contribute; the first sample of a lane (`delta_tokens`
  is null) and every truncation contribute nothing.
- **The header describes the main lane only.** `baseline_tokens`,
  `peak_tokens` and `tools` never fold in sidechain samples — a subagent's
  totals blended into the main lane's baseline would be a number nobody could
  point at. `sidechain_samples` is returned as its own ordered array.
- **Frontend**: a `ContextGrowthPanel` on the session detail page — a
  hand-rolled SVG line/area of `total_tokens` over `seq` (no charting
  dependency exists in this repo and none was added), truncation drops marked
  with a dashed rule and a dot, and beside it the ranked tool roll-up.
  Clicking a tool row switches the (now controlled) events tab and filters
  `EventTimeline` to that tool via a new `toolName` prop. An empty series
  renders no panel at all, so the hundreds of sessions recorded before this
  shipped stay visually unchanged.
- **DB**: one new table, additive:
  ```
  coding_context_samples
    id             int pk
    session_id     varchar(200) fk coding_sessions.id on delete cascade
    seq            int
    message_id     varchar(200)
    is_sidechain   bool
    at             timestamptz
    total_tokens   bigint
    output_tokens  bigint null
    delta_tokens   int null      -- null = first sample of its lane
    tools          json null
    unique (session_id, message_id)
    index (session_id, seq)
  ```
  Alembic migration `0023_coding_context_samples` on `0022_session_launch_run_id`.
  Additive, reversible, no backfill — nothing in the stored event stream can
  reconstruct a sample.

# API Contract v1.30 — factory runs list + resume from the UI

Additive on top of v1.29. Every factory run a project's runs root records is
now visible to the frontend, and a stopped, crashed or rejected run can be
resumed with one click — no terminal needed. The list reads the run dirs
themselves (`run.json`), so runs launched outside the UI show up too.

## New schemas

```
FactoryRun {
  run_id: string
  project_path: string           // the project the run worked on
  project_name: string
  state: string                  // run.json's raw state: running/stopped/finished/waiting_input
  request_text: string
  branch: string | null
  reason: string | null          // why the run ended, e.g. "cost cap reached: $32.94 of $25 budget"
  interview: boolean
  accepted: boolean
  started_at: string | null
  ended_at: string | null
  resumable: boolean             // server-side verdict, mirrors factory plan_resume's gate
}
FactoryRunResumeRequest { project_path: string, run_id: string }
FactoryRunResumeRead { run_id: string, resumed: boolean, pid: number | null }
```

## New endpoints

| Method & path | operation_id | Request | Response |
|---|---|---|---|
| GET `/api/v1/launcher/runs` | `listFactoryRuns` | — | `FactoryRun[]` (all projects under projects_root, started_at desc) |
| POST `/api/v1/launcher/runs/resume` | `resumeFactoryRun` | `FactoryRunResumeRequest` | `FactoryRunResumeRead` (400 outside projects_root, 404 unknown run, 409 not resumable, 502 spawn failure) |

## Behavior

- **`resumable`** is computed server-side, mirroring `factory/adw/runs.plan_resume`:
  false while the run is live (state `running` AND its recorded pid is alive),
  false once a run completed accepted, false without a recorded branch. A
  `running` record whose pid is dead is a crash — resumable.
- **The resume never re-imposes a cost cap.** `factory/run.py --resume <id>` reads
  budget flags from its own argv, and the spawned argv carries none — a run
  stopped at "cost cap reached" continues uncapped. The spawn is detached
  (same mechanism as `launchSession`), logs to
  `<masterwork_home>/launches/run-<run_id>.log`, and writes no DB row: the run
  dir stays the single source of truth for run state.
- **Frontend**: a `FactoryRunsCard` on SessionsListPage (outside the tabs,
  beside `InterviewQuestions`) polls `listFactoryRuns` every 5s and offers
  Resume on resumable rows.
- **DB**: none.

# API Contract v1.31 — run outcome, resume hints, session → run link

Additive on top of v1.30, correcting how a run's result is reported.

## Changed schemas

`FactoryRun` gains three fields:

```
outcome: "running" | "waiting" | "done" | "failed" | "stopped"
resume_hint: string | null     // why no resume is offered; null when resumable
session_ids: string[]          // coding sessions this run's stages reported
```

`state` stays, unchanged and raw, but **`outcome` is the field to read**.
`state` is the *process's* state, not the run's: a run whose review rejected it
and a run that was approved both end up `state: "finished"`, and a run that
crashed is left claiming `state: "running"` forever. `outcome` resolves all
three — `done` only when `accepted` is true, `failed` for a rejected or crashed
run, `stopped` for a budget/kill stop.

## New endpoint

| Method & path | operation_id | Request | Response |
|---|---|---|---|
| GET `/api/v1/launcher/runs/by-session/{session_id}` | `getRunForSession` | — | `FactoryRun \| null` |

Null, not 404, when no run owns the session — every plain chat session is that
case, and it is not an error.

## Behavior

- **The session → run link is exact, not inferred.** The factory records each
  stage's Claude session id in `<run_dir>/telemetry.jsonl`, and those ids are
  `coding_sessions.id` values. No time-window or cwd guessing is involved.
  Parsed ids are cached per file by (mtime, size), since the runs list polls.
- **`resume_hint` names the blocker** in the same terms as
  `factory/adw/runs.plan_resume`: `still running`, `completed and approved —
  nothing to resume`, or `no branch recorded — nothing safe to resume onto`.
  A run with no recorded branch is the one failure that can never be resumed.
- **Frontend**: `FactoryRunsCard` folds `done` runs away behind a "Show N
  completed runs" toggle and prints `resume_hint` where an unresumable row's
  button would be; `SessionRunBanner` puts the same badge + Resume on the
  session detail page for the run that spawned that session.
- **DB**: none.

# API Contract v1.32 — the resume gate, corrected

Fixes two wrong answers v1.31 gave. No new endpoints, no schema changes.

- **A `--no-branch` run is resumable.** v1.31 required `branch` to be set and
  reported `no branch recorded` otherwise. The factory does not work that way:
  `factory/adw/runs.py:_resume_ref` falls back to `branch_origin`, which names
  the branch such a run committed onto. Only a run that started on a detached
  HEAD (where `branch_origin` is a bare 40-char sha) has no ref to return to;
  its hint is now `ran on a detached HEAD — no branch to resume onto`.
- **A run whose branch was deleted is refused, not offered.** `plan_resume`
  checks the ref still exists; v1.31 did not, so such a run showed a Resume
  button whose spawn would refuse itself into a log file nobody reads. The
  list now reports `the branch it worked on ('X') is gone`, and
  `resumeFactoryRun` returns 409 with that same sentence before spawning
  anything. Branch names are read once per project per request, and a repo
  git cannot answer for is trusted rather than reported as gone.
- **`getRunForSession` also answers for a pipeline run's own session.** The
  runner's session id is `factory-<run_id>` (built in `factory/adw/telemetry.py`,
  read in `coding/service.py:FACTORY_SESSION_PREFIX`), which is the page the
  runs grid links to; only the stage session ids from telemetry were matched
  before, so that page showed no banner.

# API Contract v1.33 — superseded runs, and a spawn that cannot silently die

Additive on top of v1.32.

## Changed schema

`FactoryRun` gains one field:

```
superseded_by: string | null   // run id of a newer run of this same request
```

Same project, same `request_text`, later `started_at`. That is the only signal
available — the factory records no lineage between runs — and it is exactly
what the rerun button produces. The frontend offers a link to the newer run
instead of a second "Run again" on a request already running again.

## Behavior

- **`launchSession` and `resumeFactoryRun` refuse when the agent CLI is
  missing** (502): a backend started outside a login shell inherits a PATH
  without `claude`, and the factory then dies about a second after both
  endpoints have already answered "launched". The child is now spawned with a
  PATH extended by the usual per-user install dirs (`~/.local/bin`,
  `~/.claude/local`, `/opt/homebrew/bin`, `/usr/local/bin`), and a CLI that
  still cannot be found is reported instead of spawned.
- **Both endpoints read the child's log back** after a short settle and raise
  502 with the factory's own words if it already gave up — an `error:` line or
  a `NOT ACCEPTED` verdict. A run that dies on arrival is no longer reported as
  started.
- **DB**: none.

# API Contract v1.34 — dismissing a run out of the list

Additive on top of v1.33. The runs list is a working list, not an archive: a
run that nobody will act on has to be able to leave it.

## Changed schema

`FactoryRun` gains one field:

```
dismissed: boolean   // the user waved this run away; out of the way, not gone
```

## New schema and endpoints

```
FactoryRunDismissRequest { project_path: string, run_id: string }
```

| Method & path | operation_id | Request | Response |
|---|---|---|---|
| POST `/api/v1/launcher/runs/dismiss` | `dismissFactoryRun` | `FactoryRunDismissRequest` | `FactoryRun` (400 outside projects_root, 404 unknown run) |
| POST `/api/v1/launcher/runs/restore` | `restoreFactoryRun` | `FactoryRunDismissRequest` | `FactoryRun` |

## Behavior

- **The run dir is never written to.** A dismissal is masterwork's own note in
  its own table, so the factory's records stay exactly as it wrote them and a
  dismissal is undone by deleting a row. Dismissing twice is not an error.
- **Frontend**: every open row carries a × that dismisses it; dismissed runs
  fold in with the done and superseded ones behind "Show N handled runs",
  where each offers "Bring back".
- **DB**: one new table, additive:
  ```
  dismissed_runs
    id            int pk
    project_path  text
    run_id        varchar(64)
    dismissed_at  timestamptz
    unique (project_path, run_id)
  ```
  Alembic migration `0024_dismissed_runs` on `0023_coding_context_samples`.

# API Contract v1.35 — launching a chosen workflow, and the cheap "is it done?" check

Additive on top of v1.34.

## Changed schemas

```
LaunchRequest.workflow?: "full" | "plan_build" | "build_test" | "build_review" | "document" | "scout"
FactoryRun.workflow: string | null   // the preset the run recorded, e.g. "scout"
```

`workflow` is optional and omitted by default, so a plain autonomous launch's
argv is unchanged to the byte: `--workflow` is appended only when asked for,
the same rule `--run-id` and `--interview` already follow. An unknown preset is
rejected by the schema (422) rather than handed to the factory.

## Behavior

- **`scout` is the cheap one.** It is a single read-only stage
  (`factory/adw/workflows.py`) on a small model, whose role forbids writing
  files or proposing a plan: it reads the repo and reports `findings` plus a
  `summary`. That makes "is this already implemented?" answerable for a
  fraction of a plan-and-build rerun.
- **Frontend**: a stuck run offers "Check if done" beside "Run again". It
  launches the same project with `workflow: "scout"` and a request that asks
  whether the original request's work already landed, quoting it. The runs list
  labels any non-`full` run with its preset, so a check is never mistaken for a
  build.
- **DB**: none — the workflow is already recorded in the run's own `run.json`.

# API Contract v1.36 — every launch names the run it started

Corrects v1.26's "only an interview launch gets a run id".

`launchSession` now generates a run id for **every** launch and passes it as
`--run-id`, so `SessionLaunchRead.run_id` is always set and the caller knows
which run its click produced. Before, an autonomous launch answered
`run_id: null` and the UI had no way to point at the run it had just started —
the reason a started rerun or check could only say "Started" and leave the user
guessing. Interview launches are unaffected; they always worked this way.

The `--run-id` flag is the same one `factory/run.py` already accepted, so
nothing about the factory changes. What changes is that a plain autonomous
launch's argv now carries it too.

**Frontend**: once the started run reports itself in `listFactoryRuns`, the
button that started it turns into a link to that run's session page, where its
stages, events and the agent's own output already stream in. Until then it
stays an inert label, so the link never opens a page with nothing on it.

**DB**: none — `session_launches.run_id` already existed and is simply always
populated now.

---

# API Contract v1.37 — the work sync covers the sprint, not just @Me

Corrects v1.24's "read-only inbound only" sync, which only ever pulled items
`[System.AssignedTo] = @Me`: the work page never saw a teammate's item, so the
frontend's "Everyone" assignee filter had nothing to add over "@Me".

**`sync_source`** now resolves the team's current iteration path first
(`current_iteration`, unchanged as a field, just fetched earlier). When one
resolves, it queries the whole sprint —
`[System.IterationPath] UNDER '<path>' AND [System.State] NOT IN
('Closed','Removed','Done')`, every assignee — instead of `DEFAULT_WIQL`.
`DEFAULT_WIQL` (`[System.AssignedTo] = @Me`) is now only the fallback for when
no sprint covers today (or the iteration lookup fails); an operator's
`source.query_wiql` still overrides both, unchanged. The iteration path is
interpolated into the sprint WIQL with its single quotes doubled — the one
external-data interpolation the module allows, since DevOps' own sprint-lookup
API is the source, not user input.

**New field**, `WorkSource.owner_display_name: string | null` — the PAT
owner's DevOps display name, read from `GET {org_url}/_apis/connectionData`
(`authenticatedUser.providerDisplayName`) and refreshed on every sync,
best-effort like `current_iteration`: a failed lookup keeps the source's last
known value. Not settable — it has no place on `WorkSourceCreateRequest`,
only ever derived from the PAT.

```
WorkSource {
  ...                             // unchanged fields, see v1.24
  owner_display_name: string | null // PAT owner's display name; refreshed on sync
}
```

**Frontend**: `ASSIGNEE_ME` in `features/work/tree.ts` no longer means
"`!item.pulled_as_parent`" — it means `item.assigned_to` equals the item's
source's `owner_display_name`. `filterWorkItemTree` takes that name per
source id as a third argument (`OwnerNames`, keyed by `source_id`); a source
with no `owner_display_name` yet matches nobody under "@Me". The sprint
dropdown, search box, and tree building are unchanged.

**DB**: `work_sources.owner_display_name`, nullable text, added by Alembic
migration `0025_work_source_owner`.

# API Contract v1.38 — a check that reports back

Additive on top of v1.37. v1.35 could start a `scout` check but nothing came
back from it: the verdict stayed in the run dir, and the run being checked
never learned a check existed — so the button offered to ask the same question
again.

## Changed and new schemas

```
LaunchRequest.checks_run_id?: string | null   // the run this launch exists to check
FactoryRun.summary: string | null             // what this run's last stage concluded
FactoryRun.check: FactoryRunCheck | null      // the newest check started for this run

FactoryRunCheck { run_id: string, outcome: RunOutcome, summary: string | null }
```

## Behavior

- **`summary` is the run's own verdict**, read from the `detail` of the last
  stage `phase_end` in its telemetry (the closing `run` phase is bookkeeping and
  is skipped). For a `scout` run that verdict *is* the answer it was asked for.
- **The link between a check and its subject is recorded, not inferred.** A
  check asks a question about a run, so the two never share their request text
  and no matching heuristic could tie them together; `checks_run_id` is stored
  on the launch row that started the check.
- **Frontend**: a run with a check shows that check's first sentence, linking to
  its session, in place of the "Check if done" button — a run already checked is
  never checked twice. While the check runs it reads "Checking whether this
  landed anyway…". A run's own page prints its `summary` in full under the
  banner, which is where a read-only run's whole result now lives.
- **DB**: `session_launches.checks_run_id`, nullable, Alembic
  `0026_launch_checks_run` on `0025_work_source_owner` — rebased onto the head
  the work-source owner migration created rather than opening a second head.

# API Contract v1.39 — a run that is waiting, not gone

Additive on top of v1.38. Every derived status masterwork had was inferred from
absence: a run with no `SessionEnd` and no recent event was reported
`abandoned`. A run blocked on a question is silent for the opposite reason —
something is holding it — and filing it under the bucket nobody revisits is how
a question asked at 03:11 sat unanswered until the process died at 09:00.

## Changed and new schemas

```
CodingSession.status: … | "waiting_input"     // derived, and outranks abandoned
CodingSession.awaiting_input_since: string | null   // when it went blocked
GET /coding-sessions?status=waiting_input     // matches the derived status
```

## Behavior

- **`Notification` is now one of the recorded hooks** (eight, up from seven).
  It is the only Claude Code hook that fires *because* nothing is happening: a
  permission prompt, or an input box that has gone idle. Its `message` is stored
  as the event payload.
- **Only a mid-turn notification counts.** The same hook fires after a `Stop`,
  when it means "nobody has typed the next prompt yet" rather than "this run is
  stuck". The event before the notification is what separates them —
  `Stop`/`SessionStart`/`SessionEnd` mean the turn was closed, anything else
  means one was in flight. The message text is deliberately not parsed: its
  wording is the harness's to change.
- **`awaiting_input_since` is stored, and cleared by the next event** that
  proves work resumed. `SessionEnd` is the one event that does not clear it, so
  `ended_at` set *and* `awaiting_input_since` set is a run that died with its
  question still on screen.
- **`waiting_input` beats `abandoned` and narrows `running`.** Silence is only
  read as abandonment when nothing explains it, and the list filter matches the
  same three-way split so a filtered page never contradicts the cards in it.
- **Frontend**: a card wears an amber `waiting_input` chip, `Status → Waiting`
  filters to them, and a banner above the grid — outside the tabs, like the
  interview form — lists every blocked run with how long it has been waiting,
  with an opt-in desktop notification the first time a run goes blocked.
- **DB**: `coding_sessions.awaiting_input_since`, nullable timestamptz, Alembic
  `0027_coding_awaiting_input` on `0026_launch_checks_run`.

# API Contract v1.40 — pull requests, their review comments, and a session sent to fix them

Additive on top of v1.39. The work surface mirrored work items but stopped at
the point the work becomes a pull request: review comments lived only in the
DevOps web UI, and getting one fixed meant reading it there, finding the right
checkout, and retyping the comment as a prompt. This turns the PR and its
threads into rows masterwork holds, and makes "fix these comments" one button.

**Read-only, still.** Nothing here writes to Azure DevOps. Replying to a comment
and resolving a thread are a later, separately-approved change; the delegate
prompt tells the session so in as many words.

## New endpoints

```
GET  /api/v1/work/prs?source_id=            listPullRequests       -> WorkPullRequest[]
POST /api/v1/work/sources/{id}/sync-prs     syncPullRequests       -> WorkSyncResult
GET  /api/v1/work/prs/{pr_id}/threads       listPullRequestThreads -> WorkPrThread[]
POST /api/v1/work/prs/{pr_id}/delegate      delegatePullRequest    -> PullRequestDelegateResponse
POST /api/v1/work/repo-paths                saveRepoPath           -> WorkRepoPath (201)
```

## New schemas

```
WorkPullRequest {
  id, source_id, external_id,                  // external_id is DevOps' pullRequestId
  repository_id, repository_name,
  repository_remote_url,                       // the join key, never the name
  title, description,
  source_branch, target_branch,                // refs/heads/ stripped
  status, is_draft, created_by: string | null,
  external_url, external_changed_at, synced_at,
}

WorkPrThread {
  id, pull_request_id, external_id,
  status: string | null,                       // absent on some system threads
  is_resolved: boolean,                        // derived, see below
  file_path: string | null,                    // null on a PR-level thread
  right_file_line: number | null,
  comments: WorkPrThreadComment[], synced_at,
}

WorkPrThreadComment { id, author, content, comment_type, published_at }   // all nullable but content

WorkRepoPath { id, remote_url, local_path, created_at }
WorkRepoPathCreateRequest { remote_url, local_path }

PullRequestDelegateResponse {
  resolved: boolean,                           // false means nothing was launched
  remote_url, local_path: string | null,
  reason: string | null,                       // set exactly when resolved is false
  launch_id: number | null, run_id: string | null,
  prompt: string | null, unresolved_thread_count: number,
}
```

## Behavior

- **PRs sync in bulk, threads on demand.** `sync-prs` pulls the source's active
  PRs and upserts on `(source_id, external_id)`, exactly like work items. Threads
  are fetched per PR when `listPullRequestThreads` is called, because a bulk sync
  would be one HTTP round trip per open PR to fill a panel nobody opened. The
  frontend follows the same rule: a collapsed PR row issues no thread request.
- **`is_resolved` is derived, not reported.** DevOps has no boolean here, only a
  `status` string; `fixed`, `closed`, `wontFix` and `byDesign` all count as
  resolved, anything else does not. A thread with no comments is a system marker
  and never counts toward the unresolved badge.
- **A repository is matched by its remote, never by its name.** A local folder is
  routinely named differently from the DevOps repository, and DevOps reports one
  repository under several URL forms (ssh clone, https clone, the API's own
  `remoteUrl`). `repo_paths.normalize_remote_url` drops userinfo and any embedded
  PAT, rewrites ssh — including Azure's `v3/{org}/{project}/{repo}` form — to the
  https shape, lowercases the host, and drops a trailing `/` or `.git`, so both
  forms of one repo land on one key.
- **Resolution is three steps, and it learns.** Stored mapping → scan of
  `projects_root`'s immediate subfolders by their git origin → unresolved. The
  origin is read out of `.git/config` with `configparser`; nothing shells out to
  `git`. A scan hit is written to `work_repo_paths` before it is returned, so the
  next delegate for that remote is a stored hit. A stored path that no longer
  exists on disk is treated as a miss, which lets a moved checkout self-heal via
  the scan.
- **Unresolved is a normal answer, not an error.** `delegatePullRequest` returns
  `200` with `resolved: false` and a `reason` naming the root it searched. The
  frontend opens the shared `FolderPickerDialog`, `saveRepoPath` records the
  choice against the normalized remote, and the delegate is retried
  automatically — so a repository outside `projects_root` is picked once, ever,
  and every later PR on that remote resolves from the mapping.
- **The prompt carries the comments, and the limits.** `assemble_pr_prompt`
  writes the PR identity, a "check out this branch first" line, and only the
  unresolved threads (resolved ones and comment-less ones are skipped), each
  under its `file:line` heading. It closes with the rules in plain words: address
  every comment, run the repo's own checks, do **not** push, and do **not** touch
  DevOps. The launch itself reuses `launcher_service.launch` — the one spawn path
  — so a PR run is an ordinary factory run and appears in the runs list as one.
- **DevOps text is data.** PR titles, descriptions and comment bodies are stored
  verbatim and rendered as plain text — never as markdown or HTML, and never
  executed — matching how work-item descriptions have been handled since v1.20.
- **Frontend**: `Work` gains a `Backlog` / `Pull requests` tab pair driven by
  `?view=`, the same URL pattern the sessions list uses. A PR row expands to its
  threads grouped by file (PR-level threads first), resolved threads folded away
  and de-emphasised, and carries an unresolved count once opened. `Fix comments`
  is the delegate button.
- **DB**: three additive tables — `work_pull_requests`, `work_pr_threads`,
  `work_repo_paths` — Alembic `0028_work_pull_requests` on
  `0027_coding_awaiting_input`.

# API Contract v1.41 — a catalog of community skills, and installing one

Additive on top of v1.40. The assets surface could only ever show skills that
were already on disk: the ones written in a chat session and the ones an
installed Claude Code plugin shipped. Finding a skill someone else published
meant leaving masterwork for a browser, and installing it meant copying files by
hand. This adds the other half — search the community registries, read a
SKILL.md before trusting it, and write a chosen skill into the same directory the
`claude` asset provider already scans.

**Third-party data, treated as such.** Everything a registry returns is text
someone else wrote. It is rendered as plain text, never as HTML or markdown, and
a skill whose license cannot be established is badged and gated rather than
quietly installed.

## New endpoints

```
GET    /api/v1/skills/catalog?q=&limit=              searchSkillCatalog -> CatalogSearchResponse
GET    /api/v1/skills/catalog/{owner}/{repo}/{skill} getCatalogSkill    -> CatalogSkillDetail
POST   /api/v1/skills/install                        installSkill       -> InstalledSkill
DELETE /api/v1/skills/installed/{name}               uninstallSkill     -> 204
```

## New schemas

```
CatalogSkill {
  owner, repo,                                 // the GitHub source repo
  skill,                                       // slug within the repo; a GitHub hit uses the repo name
  name, description,                           // description is "" when the registry gave none
  registry: "skills_sh" | "github",
  installs: number | null,                     // only skills.sh reports one
  license: string | null,                      // SPDX id when known at search time
  license_resolved: boolean,                   // false means not looked up yet — see below
  url,
  installed: boolean,                          // a directory with this slug already exists
}

CatalogSourceError { registry, message }       // why one source's results are missing

CatalogSearchResponse { skills: CatalogSkill[], errors: CatalogSourceError[] }

CatalogSkillDetail {
  owner, repo, skill, name, registry,
  license: string | null,
  all_rights_reserved: boolean,                // true exactly when license is null
  installed: boolean,                          // a directory with this slug already exists
  installed_by_masterwork: boolean,            // false for a hand-installed skill
  skill_md,                                    // full SKILL.md text
  files: string[],                             // companion paths, relative to the skill folder
}

SkillInstallRequest { owner, repo, skill, overwrite: boolean }

InstalledSkill {
  asset_id,                                    // "claude:skill:<name>", the id the assets API uses
  name, owner, repo,
  license: string | null,
  registry, installed_at,
}
```

## Behavior

- **Two sources, merged, and a failure is partial not fatal.** A search queries
  the skills.sh no-auth endpoint and GitHub's repo search for `topic:claude-skills`
  concurrently, dedupes on `(owner, repo, skill)` case-insensitively, and returns
  200 with the sources that worked. A source that times out, 5xxs or rate-limits
  lands in `errors` instead of failing the request, because a rate-limited GitHub
  must never hide working skills.sh results. Only both sources failing is a 502.
  skills.sh wins a dedupe conflict since it carries install counts, but a license
  GitHub resolved is carried onto the winning record rather than thrown away.
- **`license_resolved` is the difference between "unlicensed" and "unknown".**
  skills.sh reports no license, so its search hits arrive `license: null,
  license_resolved: false`, which the UI shows as *License unknown*. GitHub
  reports the license explicitly, and a repo it reports as `null` or
  `NOASSERTION` really is all rights reserved. Only a resolved null is shown as
  such, and `getCatalogSkill` always resolves it — which is why a preview is
  required before the risky install path can be taken.
- **An unlicensed skill needs a second click.** `all_rights_reserved` is sent as
  its own boolean rather than left for the client to infer from `license === null`,
  so a missing field can never read as permissive. The install button on such a
  skill re-arms into a risk-naming confirmation instead of installing on the
  first press.
- **"Is my copy current?" is answered by content, not by a version.** Skill
  frontmatter has no standard version key — most skills declare none at all, and
  the ones that do disagree on where it lives (`version`, or nested under
  `metadata`). `version` and `installed_version` are therefore best-effort and
  usually null, while `differs_from_installed` compares the registry's SKILL.md
  against the copy on disk and works for every skill. It is null when nothing is
  installed, so the client can tell "no copy" from "identical copy".
- **The dates cost two requests, and are allowed to fail.** `created_at` and
  `last_modified_at` come from the commits API filtered to the skill folder: the
  newest commit is one request, and its `Link` header names the last page, whose
  single entry is the oldest commit. That takes a preview from two API requests
  to four, so the lookup degrades to nulls on any failure — losing the dates must
  never cost the SKILL.md. `last_change_summary` is the commit subject only,
  capped and rendered as plain text like every other registry string.
- **`url` points at the skill, not the repo.** The tree read already knows which
  folder the SKILL.md came from, so the link deep-links to it — often several
  levels down, e.g. `/tree/HEAD/skills/engineering/grill-with-docs`.
- **Already-installed is a state, not an error.** Install keys on the directory
  name under the skills root, so both the search rows and the detail report
  whether that slug is taken; the UI offers a guarded reinstall rather than
  letting the request 409. The refusal is checked before the fetch, so
  discovering it costs no GitHub round trip. `installed_by_masterwork`
  separates a skill masterwork wrote from one that was already there — only the
  former can be uninstalled here, so the UI must not offer removal for the
  latter.
- **A spent GitHub quota is its own answer.** Anonymous GitHub allows 60
  requests an hour, which a single browse can exhaust, so a 403/429 carrying
  `x-ratelimit-remaining: 0` becomes a 429 whose message names `GITHUB_TOKEN`
  as the remedy rather than passing GitHub's raw body to the UI. It is never
  retried — the quota will not refill within a request — and the nested-folder
  fallback lets it propagate instead of reporting it as a missing skill.
- **Four skills are refused outright.** `anthropics/skills` ships `docx`, `pdf`,
  `pptx` and `xlsx` under a license that forbids extracting them; the refusal is
  checked before any network call, not after fetching.
- **A fetch costs two API requests, whatever the layout.** One recursive tree
  read resolves the skill folder *and* lists it with every file's size, and the
  bytes come from `raw.githubusercontent.com`, which is outside the API quota —
  so the file count no longer affects the cost. The only other request is the
  license lookup. Probing candidate paths one at a time cost up to eight
  requests for the same skill, which one browse could turn into a spent
  anonymous quota. A repo too large for a single tree response reports
  `truncated` and falls back to walking the contents API.
- **The fetch is bounded in four ways.** 5 MiB total, 200 entries, 5 directory
  levels, and any entry whose resolved path escapes the skill folder is a refusal
  rather than a skip. Symlinks and submodules are refused, not followed. Reading
  the tree first means the size and escape guards run against the listing, so an
  oversized or escaping skill is refused before a single byte is downloaded.
- **Installs are atomic, and never escape the skills root.** The tree is staged
  in a sibling directory and swapped in with `os.replace`, so a failed fetch
  leaves no half-written skill folder; an overwrite moves the old directory aside
  and restores it if the write fails. A slug that is not plain lowercase-kebab is
  rejected before anything is written.
- **Uninstall only removes what masterwork installed.** The install row is the
  permission: a skill directory with no matching row is not deleted, so a
  hand-written skill can never be removed through this endpoint.
- **No new read path.** An installed skill lands in `settings.claude_skills_root`,
  which the existing `claude` asset provider already scans, so it appears in the
  assets list with no change to that provider.

# API Contract v1.42 — where a skill lives, which agents load it, and making one generic

Additive on top of v1.41. Every skill masterwork listed was a Claude Code skill,
because `~/.claude/skills` was the only skills folder it scanned. A machine with
more than one coding agent has more than one: Codex reads `~/.codex/skills`, and
the Agent Skills layout that skills.sh installs into, `~/.agents/skills`, is the
one folder every agent can share. This makes the folder visible on every asset,
scans the other two, and adds the one write that moves a skill from an agent's
own folder into the shared one — without leaving a second copy behind.

## New endpoint

```
POST /api/v1/assets/{asset_id}/migrate   migrateAssetToGeneric(AssetMigrateRequest?) -> AssetMigrationResult
```

## Changed and new schemas

```
AssetSummary {
  ...,
  provider: "claude" | "claude-plugin" | "codex" | "generic" | "masterwork",   // "codex" and "generic" are new
  agents: string[],              // NEW — coding agents that load this asset: "claude", "codex"
}

AssetMigrateRequest { replace_generic: boolean }   // optional body; default false

AssetMigrationResult {
  asset: AssetDetail,            // the skill at its new "generic:skill:<name>" id
  previous_id,                   // the id it had before the move
  linked_agents: string[],       // agents whose skills dir now links to the generic copy
  skipped_agents: string[],      // agents that already had an unrelated skill of this name
  claude_only_keys: string[],    // frontmatter keys kept that only Claude Code honours
  name_rewritten: boolean,       // `name:` was added or changed to match the folder
  relinked_projects: number,     // project links re-pointed from the old id to the new one
  adopted: boolean,              // the generic folder already held an identical copy; nothing was copied
  replaced_generic: boolean,     // a differing generic copy was replaced, on request
}
```

## Behavior

- **Two more providers, one skill scanned once.** `codex` scans
  `~/.codex/skills` (skipping Codex's hidden `.system` folder, which is Codex's,
  not the user's) and `generic` scans `~/.agents/skills`. Claude Code and Codex
  only read their own folder, so a generic skill reaches an agent through a
  symlink in that agent's folder. The `claude` and `codex` providers skip any
  entry that resolves into the generic root, so the skill is listed once, under
  the provider that owns the real files, and never as a duplicate.
- **`agents` is what the UI reads, `provider` is where the files are.** A
  Claude skill lists `["claude"]`, a Codex skill `["codex"]`. A generic skill
  lists the agents whose folder actually links to it — which can be fewer than
  all of them, so "generic" never silently means "reaches everyone". A factory
  role lists none.
- **Migration copies, then swaps, then links.** The skill folder is copied into
  the generic root under a staging name and moved into place; the source folder
  is then replaced by a symlink to it, and every other agent's folder gets a
  symlink too unless it already holds something of that name (reported in
  `skipped_agents`, left alone). The skill therefore lives on disk exactly once,
  and every agent still finds it. A failure before the swap leaves the source
  untouched; a failure after it leaves a complete generic copy.
- **The generic format is the same file, with `name` guaranteed.** The Agent
  Skills spec requires `name` and that it match the folder, so the move adds or
  corrects that one line and changes nothing else — a folded description block
  and key order survive byte for byte. Claude-only keys
  (`disable-model-invocation`, `argument-hint`, `model`, …) are kept, because
  other agents ignore keys they do not know while stripping them would change
  how Claude uses the skill; they are returned in `claude_only_keys` so the UI
  can say so.
- **The id changes, and links follow it.** The skill is `claude:skill:<name>`
  before and `generic:skill:<name>` after. Every project that linked the old id
  is re-pointed in the same request (`relinked_projects`), so a project's asset
  list never dangles. Usage rollups are keyed by name and need no change.
- **A copy that is already there is adopted, not refused.** skills.sh-style
  installs leave the same skill in both `~/.claude/skills` and
  `~/.agents/skills`, which is exactly the duplicate this endpoint exists to
  remove. When the generic folder already holds a byte-identical tree, nothing
  is copied: the source folder becomes the link and `adopted` is true. When the
  generic copy differs, the request is refused with a 409 whose detail says so
  ("differs"), and only an explicit `replace_generic: true` throws that copy
  away in favour of this one (`replaced_generic`). The UI must re-arm into a
  confirmation naming the loss before sending that flag.
- **What cannot move is a 409, not a silent no-op.** An agent file (no
  cross-agent format), a plugin asset (its marketplace owns it), a factory role,
  and a skill that is already generic all answer 409; so does a source folder
  that is already a link. An unknown id stays 404.
- **Snapshots as for any write.** The source tree (`~/.claude` or `~/.codex`) is
  committed before and after when the user made it a repo; `~/.agents` likewise.
  Neither is ever turned into a repo behind the user's back.

# API Contract v1.43 — switching a skill off without deleting it

Additive on top of v1.42. Until now the only way to stop a coding agent loading
a skill was to delete the folder or move it by hand, and both lose the skill's
history in the UI. Coding agents read `<skills_root>/<name>/SKILL.md` one level
deep and nothing else, so a folder parked under `<skills_root>/.disabled/` is
invisible to every one of them while staying readable, editable and one rename
away from coming back. This exposes that state on every asset and adds the one
write that flips it.

## New endpoint

```
PUT /api/v1/assets/{asset_id}/enabled   setAssetEnabled(AssetEnabledRequest) -> AssetDetail
```

## Changed and new schemas

```
AssetSummary {
  ...,
  disabled: boolean,             // NEW — parked under .disabled/; no agent loads it
}

AssetEnabledRequest { enabled: boolean }
```

## Behavior

- **The id survives; the path moves.** `claude:skill:<name>` is
  `~/.claude/skills/<name>/SKILL.md` while enabled and
  `~/.claude/skills/.disabled/<name>/SKILL.md` while disabled; Codex likewise
  under `~/.codex/skills`. Every read, edit, chat, diagram and snapshot keeps
  working on the parked path, and project links need no re-pointing.
- **A disabled skill claims no agent.** Its `agents` is `[]` whatever its
  provider, because nothing loads it — the UI must not read that as a generic
  skill nobody linked yet; `disabled` is the flag that tells the two apart.
- **A generic skill moves with its links.** The real folder goes to
  `~/.agents/skills/.disabled/<name>`, and every agent link that resolved to it
  moves to that agent's own `.disabled/<name>`, re-pointed at the new home.
  Enabling reverses it and recreates exactly the links found parked: an agent
  that never linked the skill does not gain a link on the way back. The parked
  links are not listed as skills of their own.
- **Search still finds it.** Disabled skills are listed and searched like any
  other; a `.disabled/` folder itself is never an asset.
- **Idempotent.** Setting the state the skill already has is a 200 with the
  asset unchanged and nothing touched on disk.
- **Never overwrites, never half-moves.** A destination that already exists —
  the parked folder, or an agent's parked link — is a 409 before anything moves.
  Renames are atomic, and a failure partway through a generic toggle is rolled
  back so the tree is either fully toggled or as it was.
- **What cannot be toggled is a 409.** An agent file (no `.disabled` convention
  for agents yet) and a plugin asset (its marketplace owns the folder) both
  answer 409 with a message saying so. An unknown id stays 404.
- **Snapshots as for any write.** Every tree the toggle touches (`~/.claude`,
  `~/.codex`, `~/.agents`) is committed before and after where the user made
  it a repo; none is ever turned into one behind their back.

# API Contract v1.44 — is my installed skill still what I installed, and is upstream ahead of it?

Additive on top of v1.43. A skill installed from the catalog can drift two ways:
the user edits it, or the repo it came from moves on. Until now nothing said
which, and the only remedy was a blind reinstall. This records a baseline at
install time, adds an on-demand check that classifies the drift, and an update
that reinstalls from upstream without ever overwriting local edits by accident.

## New endpoints

```
GET  /api/v1/skills/installed                 listInstalledSkills() -> InstalledSkill[]
POST /api/v1/skills/installed/{name}/check    checkSkillUpstream() -> UpstreamCheckResult
POST /api/v1/skills/installed/{name}/update   updateSkillFromUpstream(SkillUpdateRequest) -> InstalledSkill
```

## Changed and new schemas

```
InstalledSkill {
  ...,
  source_url: string,                  // NEW — the skill's folder on GitHub
  installed_sha: string | null,        // NEW — upstream commit for the folder at install time
  root_path: string | null,            // NEW — folder inside the repo; "" for the repo root
  last_checked_at: datetime | null,    // NEW — cache of the last check, null until one has run
  upstream_sha: string | null,         // NEW
  drift_status: DriftStatus | null,    // NEW
}

DriftStatus = "current" | "edited_locally" | "upstream_changed" | "diverged" | "unknown_origin"

UpstreamCheckResult {
  name, status: DriftStatus, checked_at,
  source_url: string | null,
  installed_sha: string | null,
  upstream_sha: string | null,                   // newest upstream commit touching the folder
  upstream_last_modified_at: datetime | null,
  upstream_last_change_summary: string | null,   // commit subject — third-party text, plain only
  skill_md_diff: string,                         // unified diff, installed -> upstream; "" when identical
  other_changes: UpstreamFileChange[],           // companion files: {path, change: "added"|"removed"|"changed"}
}

SkillUpdateRequest { force: boolean }   // default false
```

## Behavior

- **An install records where it came from and what it wrote.** `installed_sha`
  is the newest commit touching the skill folder, read from the commits API the
  same way the preview's dates are; `installed_tree_hash` (server-side only) is
  a sha256 over the installed files' sorted paths and bytes, computed from what
  landed on disk; `root_path` is the folder the tree read resolved, so a later
  check re-fetches exactly that folder rather than re-guessing the layout. The
  sha lookup is allowed to fail — it leaves null and the install goes ahead.
- **The check is user-initiated, and the list never costs GitHub.** A check
  re-fetches the folder (the same two API requests as a preview, plus the
  commits lookup) and writes its result onto the row. `GET /skills/installed`
  returns that cache, so a list can badge every installed skill from one request
  and a page load never spends quota. The UI must not check on mount.
- **Two baselines, one status.** The tree hash says whether the local copy
  changed; the sha says whether upstream did. `current` is neither,
  `edited_locally` and `upstream_changed` are one each, `diverged` is both. When
  either sha is unknown, content stands in: upstream counts as changed when its
  files no longer hash to what was installed. A row from before v1.43 has no
  baseline at all, so the check can only compare the two copies directly: equal
  is `current`, and anything else is `diverged` — the status that makes the
  update ask first — because nothing can say whose change it is.
- **`unknown_origin` is an answer, not an error.** A name with no install row
  (a hand-written skill) and a folder upstream no longer has both return 200
  with that status; the latter is cached on the row so the list can show it.
- **The diff is SKILL.md only; other files are listed.** `skill_md_diff` is a
  unified diff of the installed SKILL.md against upstream, rendered as plain
  text by the client. Companion files are reported by path and kind of change
  rather than diffed — they may be binary.
- **Update is a reinstall, guarded by the status.** It reruns the comparison
  and refuses with 409 when the status is `edited_locally` or `diverged` unless
  `force` is true; the UI must re-arm into a confirmation naming the loss before
  sending it. The new tree goes through the same staging-then-`os.replace`
  swap as an install, so a failed fetch or a failed write leaves the old copy
  in place and the row untouched. A successful update resets the baseline and
  caches `current`. A skill masterwork did not install answers 404; a folder
  upstream no longer has answers 404 too, since there is nothing to update to.
- **Snapshots as for any write.** The skills tree is committed before and after
  the update when the user made it a repo, so the overwritten copy stays
  diffable and revertible there.

# API Contract v1.45 — Codex sessions, recorded the way Claude Code's are

Additive on top of v1.44. Session recording was Claude Code only: one
`Integration`, one forwarder, one hook vocabulary. Codex has had hooks of its
own since spring 2026 — `~/.codex/hooks.json`, the same `{"hooks": {"<Event>":
[{"matcher", "hooks": [...]}]}}` shape, a near-identical event list — so it gets
the same treatment: a second card on the Sessions screen, a second forwarder,
and a session row that says which agent ran it.

## No new endpoints

`GET /api/v1/observability/integrations` now lists two entries, `claude-code`
and `codex`; `connect`/`disconnect` take either id. `POST /api/v1/hooks/events`
gains one optional field.

## Changed schemas

```
HookEventRequest {
  ...,
  source: "claude-code" | "codex",   // NEW — default "claude-code"; used on first sight only
}

CodingSession / CodingSessionDetail {
  ...,
  source: string,   // was 'always "claude-code"'; now "claude-code" | "codex"
}

AssetCall.source   // spawn_call now also covers a Codex SubagentStart (carries agent_type,
                   // agent_id); skill_read now also covers a Codex shell command that
                   // prints a SKILL.md
```

## Behavior

- **`source` is set once, by the forwarder that created the session.** Every
  Codex event says `source: "codex"`; a Claude Code forwarder says nothing and
  gets the default, so an install that never upgrades its hooks keeps filing
  where it always did. The value is not an enum on the read side: a session
  recorded by an agent this backend has not heard of still reads back. A value
  the ingest does not know on the *write* side is dropped for the default, not
  422'd — same posture as every other optional hook field.
- **Codex's hook vocabulary maps onto the same turns and lanes.** `SessionStart`,
  `UserPromptSubmit`, `PostToolUse`, `Stop`, `SessionEnd` mean what they mean
  for Claude Code. `SubagentStart` opens a span on the subagent's own lane
  (there is no spawn tool to watch), `SubagentStop` closes it. `Interrupt`
  closes the main lane's turn as `abandoned` — the person cut it short, and it
  will never get a `Stop`. `PermissionRequest` is Claude Code's permission
  `Notification`: mid-turn it puts the run in `waiting_input`, after a
  `Stop`/`Interrupt` it is nothing. `PreCompact`/`PostCompact` and anything
  else Codex adds later are stored as events in no lane, never dropped.
- **Skill attribution reads shell commands under every skills root.** Codex has
  no Skill tool and no Read: it loads a skill by printing the file, so a
  `PostToolUse` from one of its shell tools (`exec_command`, `exec`, `shell`,
  …) whose command names `<root>/skills/<name>/SKILL.md` counts as a
  `skill_read`, with the path as its input. The roots recognised are
  `.claude/skills`, `.codex/skills` and `.agents/skills`, wherever they sit —
  which also means a Claude Code `Read` of a shared `~/.agents/skills` skill
  now counts, where before only `.claude/skills` did. Claude Code's `Bash` is
  deliberately not read this way.
- **Cost is null for a model without a known price.** The Codex forwarder reads
  the thread's running token totals off the rollout file (`token_count`
  lines, cumulative — the last one is the total) and prices them only for
  models on its short list of published rates. `gpt-6-*` is not on it;
  `tokens_*` and `cache_read_tokens` land regardless, `cost_usd` stays null.
- **Launch mode knows `codex exec`.** A `launched_by` chain naming `codex exec`
  classifies the run `automated`, as `claude -p` does.
- **Connecting Codex writes `~/.codex/hooks.json` only.** `config.toml` is
  read, never written, for one thing: `[features] hooks = false`, which makes
  the integration `unavailable` with a detail saying so, rather than a
  "connected" card recording nothing. Hooks are on by default in Codex, so no
  flag is ever set.
