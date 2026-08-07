# Masterwork — backend

FastAPI service that browses/searches/edits the Claude Code skills and subagents
installed under `~/.claude`, and runs a `claude -p` chatbot that proposes file
changes the user can accept or reject. No auth — single-user local tool.

## Run

```bash
uv sync
cp .env.example .env            # already committed for local dev
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --port 8008
```

- API base path: `/api/v1`  ·  OpenAPI: `http://localhost:8008/openapi.json`
- CORS origin: `http://localhost:5192` (the Vite frontend)

## Layout

- `app/providers/` — asset source abstraction (`ClaudeProvider`; add Cursor/Codex here)
- `app/api/v1/{assets,chat,proposals}/` — routes → service → (repository) per feature
- `app/services/claude_runner.py` — async `claude -p` subprocess wrapper
- `app/services/proposal_parser.py` — extracts the trailing ```proposal``` block
- `app/db/` — SQLAlchemy async models + session; chat state only (assets are on disk)

## Test

```bash
uv run ruff check .
uv run python -m pytest
```

Integration tests use a dedicated `masterwork_test` Postgres database
(created/dropped per session) — never a mock.
