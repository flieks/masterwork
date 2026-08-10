# Masterwork — Product Spec (v1)

## What it is

A local web app for developers to manage everything globally installed for their
AI coding tools: skills and subagents (Claude Code today; Cursor/Copilot/Codex
later). Browse them, search them, edit their markdown, and refine them through a
chatbot that proposes changes the user can accept or reject.

## Domain model

- **Asset** — one installed skill or agent. Source of truth is the file on disk;
  nothing about assets is stored in the DB.
  - `kind`: `skill` | `agent`
  - `provider`: which tool it belongs to (`claude` only in v1)
  - Claude provider roots: skills `~/.claude/skills/<name>/SKILL.md`,
    agents `~/.claude/agents/<name>.md`
  - `id` is the stable slug `"{provider}:{kind}:{name}"`, e.g. `claude:skill:frontend-dev`
  - title/description parsed from YAML frontmatter when present, else derived from filename
- **ChatSession / ChatMessage / Proposal** — stored in the app database (see API
  contract). A proposal is a set of concrete file changes suggested by the
  assistant, pending until the user accepts (backend applies them) or rejects.
  - The store is **SQLite at `~/.masterwork/masterwork.db`** by default, so a
    fresh `npx masterwork` needs no database server and survives a re-clone.
    Point `DATABASE_URL` at Postgres (`postgresql+asyncpg://…`) to use that
    instead — both dialects are supported and migrated by the same revisions.

## Screens

1. **Skills** (`/skills`) and **Agents** (`/agents`)
   - Toggle between **grid view** (cards) and **table view** (rows); choice persists (localStorage).
   - Card/row: title, description, provider badge, last-updated date.
   - **Search box**: matches title, description, *and full file content* (server-side, debounced).
   - Click → detail page.
2. **Asset detail** (`/skills/:name`, `/agents/:name`)
   - Rendered markdown view; metadata (path, provider, updated).
   - **Edit** mode: markdown editor (CodeMirror), Save (PUT) / Cancel.
3. **Chat** (`/chat`, `/chat/:sessionId`)
   - Sidebar: sessions list (newest first), new session, delete session (confirm).
   - Messages with timestamps, markdown-rendered; input box; visible "thinking"
     state (a claude -p round trip can take 30–120 s).
   - The assistant knows all installed skills/agents (it can read `~/.claude`).
   - When the assistant proposes changes, the message carries a **proposal card**:
     summary + per-file changes (path, action, description, collapsible new
     content). Buttons: **Accept** (backend applies the changes) / **Reject**.
     Refining = just replying in the chat; the assistant issues a new proposal.

## Chatbot mechanics

- Backend runs `claude -p` (subscription, no API key) with `--model opus`
  (Opus 4.8), read-only tools, cwd `~/.claude`.
- Multi-turn: first exchange stores the CLI's `session_id`; later messages use
  `--resume`.
- Proposals travel as a trailing ```proposal fenced JSON block in the reply;
  the backend parses it out, stores it, and strips it from the visible text.
- Apply is done by the **backend itself** (not claude) after user acceptance,
  with strict path validation (must resolve inside a provider root).

## Non-goals (v1)

- Auth (single-user local tool), streaming chat responses, non-Claude providers
  (but the provider interface must make adding them trivial), project-local
  (non-global) assets.

---

# v1.1 — Projects & Mermaid diagrams

## Projects

A **Project** is a persistent workspace for a scenario the user wants their
skills/agents to support (e.g. "new repo → auto-deploy to Azure, Clerk auth,
Vercel frontend hosting, GitHub repo"). It holds: a name, a **goal** (markdown
scenario description), the **linked assets** (skill/agent ids serving the
scenario), and a **flow diagram** (Mermaid) showing how those assets work
together.

Chat merge decision: chat sessions gain an optional `project_id`. The global
**Chat** screen keeps showing only unscoped sessions; each project page embeds
its own scoped sessions reusing the same chat components. Project-scoped chat
knows the project (goal, linked assets, diagram) and can propose project
updates (link/unlink assets, rewrite goal, update flow diagram) via the same
propose → accept/reject mechanism as file changes.

### Screens

4. **Projects** (`/projects`): card list (name, goal excerpt, asset count,
   updated), "New project" dialog (name + goal), delete with confirm (warns
   that the project's chats are deleted too).
5. **Project detail** (`/projects/:id`), two tabs:
   - **Overview**: editable name/goal, linked skills & agents (chips grouped by
     kind, click → asset detail), flow diagram rendered with Mermaid (+ raw
     editor), empty states nudging the user to ask the project chat to analyze
     the scenario and propose assets + diagram.
   - **Chat**: identical UX to the global chat, scoped to the project.

## Mermaid everywhere

- **MermaidView** component: renders mermaid source (theme-aware, parse errors
  fall back to the raw code block).
- Markdown rendering (chat messages, project goal) renders ` ```mermaid `
  fences as diagrams.
- **Asset detail** gets a Diagram section: "Generate diagram" runs a one-shot
  claude -p that reads the file and returns a mermaid flowchart of how the
  skill/agent works internally; cached in the app database by file hash, with a
  "regenerate (file changed)" hint when stale.

---

# v1.8 — Global CLAUDE.md

The instructions file every Claude Code session loads (`~/.claude/CLAUDE.md`)
sits alongside the skills and agents it governs, so the app shows it too.

6. **CLAUDE.md** (`/instructions`, sidebar entry): rendered markdown view of the
   global instructions file, with the same Edit → CodeMirror → Save/Cancel flow
   (and unsaved-changes guard) as asset detail. When the file does not exist
   yet, an empty state offers to create it.

It is deliberately **not** an asset: it has no frontmatter, no id, and lives
outside the provider roots — which keeps chat proposals unable to write it.
