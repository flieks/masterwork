# Dev setup

The [README](../README.md) quick start is all you need to run Masterwork. This
file covers the optional extras.

## Ports

| Service  | Port | Command                                                          |
|----------|------|------------------------------------------------------------------|
| frontend | 5192 | `cd frontend && npm run dev`                                     |
| backend  | 8008 | `cd backend && uv run uvicorn app.main:app --reload --port 8008` |

Both are configurable: the backend port via the uvicorn flag, the frontend's
view of it via `VITE_API_URL` in `frontend/.env`.

## Database

SQLite by default, at `~/.masterwork/masterwork.db`. Nothing to install:

```bash
cd backend && uv run alembic upgrade head
```

Three pragmas are set on every SQLite connection, in `app/db/session.py`:
`foreign_keys=ON` (cascade deletes are silently ignored without it),
`journal_mode=WAL` (a long simulation write must not block reads), and
`busy_timeout` (wait rather than raise "database is locked").

To use Postgres instead, set `DATABASE_URL` in `backend/.env`:

```
DATABASE_URL=postgresql+asyncpg://localhost:5432/masterwork
```

then `createdb masterwork && uv run alembic upgrade head`. The same revisions
run on both — `JSONColumn` and `UTCDateTime` in `app/db/types.py` absorb the
dialect differences (JSONB vs JSON, and SQLite's lack of a native timestamptz).

Tests follow whatever `DATABASE_URL` says: a throwaway SQLite file by default,
or a `masterwork_test` database created and dropped per run on Postgres. Neither
ever touches your dev database.

## Regenerating the API client

The frontend's TypeScript client is generated from the backend's OpenAPI schema
and is not checked in. With the backend running:

```bash
cd frontend && npm run generate:api:local
```

## Running as background services (macOS)

Optional. Handy if you want the servers up at login rather than babysitting two
terminals. Create two LaunchAgents:

`~/Library/LaunchAgents/masterwork.backend.plist`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>masterwork.backend</string>
  <key>KeepAlive</key><true/>
  <key>RunAtLoad</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>WorkingDirectory</key><string>/PATH/TO/masterwork</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>-lc</string>
    <string>cd /PATH/TO/masterwork/backend &amp;&amp; mkdir -p ../.dev-logs &amp;&amp; exec /PATH/TO/masterwork/backend/.venv/bin/python -m uvicorn app.main:app --port 8008 &gt;&gt; ../.dev-logs/backend.log 2&gt;&amp;1</string>
  </array>
</dict>
</plist>
```

The frontend agent is the same shape, running `npm --prefix frontend run dev`
on port 5192.

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/masterwork.backend.plist  # start
launchctl kickstart -k gui/$(id -u)/masterwork.backend                            # restart
launchctl bootout   gui/$(id -u)/masterwork.backend                               # stop
```

Logs land in `.dev-logs/`.

Two gotchas learned the hard way:

- **Keep the repo out of `~/Documents`.** It is TCC-protected on macOS, and
  launchd services cannot read file contents there.
- **The backend agent execs `.venv/bin/python` directly** rather than going
  through `uv run` — uv's environment resolution misbehaves under launchd. After
  changing backend dependencies, run `uv sync`, then restart the service.

`scripts/dev.sh` is a manual alternative. launchd will win any fight over the
ports, so `bootout` first if you want manual control.
