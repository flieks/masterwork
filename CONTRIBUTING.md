# Contributing

Thanks for looking. Issues and PRs are welcome — especially **new provider
support** (see below), which is the most useful thing anyone can add right now.

## Setup

```bash
# backend — SQLite by default, no database server needed
cd backend
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --port 8008

# frontend, in a second terminal
cd frontend
npm install
npm run dev        # http://localhost:5192
```

You need Python 3.13+, [uv](https://docs.astral.sh/uv/), Node 20+, and the
Claude Code CLI signed in (the assistant shells out to it).

## Running the checks

CI runs exactly these, so run them before opening a PR:

```bash
cd backend
uv run ruff check . && uv run ruff format --check . && uv run mypy app && uv run pytest

cd ../frontend
npm run typecheck && npm run lint && npm run build && npm run test:ct
```

### Two things that catch people out

**The app must work on SQLite *and* Postgres.** SQLite is what a fresh install
gets; Postgres is opt-in via `DATABASE_URL`. They differ in ways that fail
silently — SQLite ignores `ON DELETE CASCADE` without a pragma, and has no
native `timestamptz`. `app/db/types.py` and `app/db/session.py` absorb those
differences; add to them rather than special-casing at a call site. CI runs the
suite on both, and so can you:

```bash
DATABASE_URL="sqlite+aiosqlite:///:memory:" uv run pytest    # SQLite
DATABASE_URL="postgresql+asyncpg://localhost:5432/masterwork" uv run pytest
```

**If you change the API, regenerate the client.** The frontend's TypeScript
client is generated from the backend's OpenAPI schema, and both the schema and
the client are committed. With the backend running:

```bash
curl -s localhost:8008/openapi.json | python3 -m json.tool > frontend/openapi.json
cd frontend && npm run generate:api:local
```

CI fails if either is stale.

## Adding a provider

The whole point of `SKILL.md` being an open standard is that these files work
across Claude Code, Codex, Cursor, Gemini CLI and others. The backend already
routes asset scanning through a provider abstraction — today only the `claude`
provider exists, reading `~/.claude/skills` and `~/.claude/agents`.

Adding another is the natural first contribution: implement the provider
interface for your tool's asset directory, register it, and the whole UI
(browse, edit, chat-refine, simulate) works against it unchanged. Open an issue
first if you want to talk through where a tool's assets live.

## Pull requests

- One concern per PR; small is easier to merge than complete.
- Explain *why* in the description — the what is visible in the diff.
- Tests for behaviour changes. Integration tests use a real database, never mocks.
- Keep comments brief and about intent, matching the surrounding code.

## Reporting bugs

Include your OS, Python and Node versions, which database you're on, and which
agent CLI's assets you're pointing at. The issue templates ask for these.
