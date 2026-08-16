# Context-growth series for coding sessions

## The change, in one paragraph

Claude Code's transcript already says how the context window filled up — every
assistant message carries a cumulative `usage` block — and the forwarder throws all
of it away except the sum. This change keeps the shape of that curve. A
`context_samples()` in `backend/app/observability/forwarders/claude_code.py` walks
the same transcript `transcript_usage()` walks, dedupes by assistant message id
exactly as it does, and returns one ordered sample per API response:
`{seq, message_id, at, total_tokens, output_tokens, model, is_sidechain, tools[]}`,
where `tools[]` names the tool results that landed in context since the previous
assistant message, resolved by `tool_use_id`. The list rides the existing
Stop/SessionEnd hook body under a new `context_samples` key. The backend stores it in
a new **reported** (not derived) table `coding_context_samples`, upserted by
`(session_id, message_id)`, with a per-lane `delta_tokens` recomputed on every
ingest; `GET /api/v1/coding-sessions/{session_id}/context`
(`readSessionContextSeries`) returns the main lane's ordered samples, the sidechain
samples as their own series, and a header carrying `baseline_tokens`, `peak_tokens`
and a per-tool roll-up of summed positive deltas. Negative deltas — a real context
truncation, observed as a −7570 step — are stored, flagged and kept out of the
roll-up. The frontend adds a `ContextGrowthPanel` to the session detail page: an SVG
area/line of `total_tokens` over `seq` with truncation drops marked, and beside it
the ranked tool roll-up whose rows filter the existing `EventTimeline` to that tool.

## State of the working tree — read this before building

**The implementation is already present in this checkout.** Every file listed below
exists and carries the described code; `docs/API_CONTRACT.md`, `frontend/openapi.json`
and `frontend/src/api/generated/api.ts` already carry the contract (as **v1.29**, see
assumption 1), and `backend/alembic/versions/0023_coding_context_samples.py` is the
only new head, sitting on `0022_session_launch_run_id`. Verified by reading the files,
not by running anything — this stage has no shell.

So the build stage's job is **verification and repair, not re-creation**. Do not
rewrite what is already there and do not renumber the migration or the contract
section. Run the gates, fix what is red, and leave the rest alone:

```
cd backend  && uv run ruff check . && uv run ruff format --check . \
             && uv run mypy app && uv run pytest
cd frontend && pnpm typecheck && pnpm test:ct
```

One concrete suspect, worth checking first: `backend/alembic/versions/0023_coding_context_samples.py`
lines 52–54 split `op.drop_index(…)` over three lines with **no magic trailing comma**,
and the joined form is ~96 chars against `line-length = 100`. `ruff format --check` will
most likely want it on one line. The `op.create_index(…)` above it does have the
trailing comma and is safe.

## Files to add or change

### Forwarder (stdlib-only, fail-silent — keep it that way)

| Path | Why |
|---|---|
| `backend/app/observability/forwarders/claude_code.py` | `context_samples(path) -> list[dict]` beside `transcript_usage()`, posted from `build_body()` under `body["context_samples"]` in the same `event in ("Stop", "SessionEnd") and raw.get("transcript_path")` branch that already sets `body["stats"]`. The key is added **only when the list is non-empty**, so an unreadable transcript leaves the autonomous hook payload byte-for-byte as it is today. No import beyond the stdlib the module already uses; the file stays runnable under a bare `python3`. |

`context_samples()` in detail — this is the part with the traps:

* One pass over the JSONL, the same `try/except ValueError` per line for the
  half-written tail and the same `except OSError: return []` for a missing file.
* **Two maps, one walk.** `tool_use_id -> tool name` is collected from *every*
  assistant line's `message.content` blocks of `type == "tool_use"` — **not** only
  from first-seen message ids. One API response spans several lines (text on one, the
  `tool_use` on the next) carrying the same `message.id`, so the message-id dedupe
  that keeps usage from doubling must not also swallow the tool names.
* Pending tool names accumulate from `type == "user"` lines whose `message.content`
  holds `tool_result` blocks; each block's `tool_use_id` is looked up in the map and
  appended by name. An id that resolves to nothing is skipped — never invented, never
  posted as a raw id.
* A sample is emitted when an assistant line carries a `dict` `usage` and a `str`
  `message.id` not yet seen. It takes the pending tool names, then clears them.
  `total_tokens = input_tokens + cache_read_input_tokens + cache_creation_input_tokens`
  (each `int(... or 0)`, as `transcript_usage` already does). An assistant message
  with no `usage` block emits no sample and does **not** clear the pending list.
* **Lanes are kept apart at the source**: `is_sidechain = bool(record.get("isSidechain"))`,
  and the pending-tools accumulator is keyed by that flag, so a subagent's
  `tool_result` can never be attributed to the main lane's next message.
* `seq` is a single 1-based monotonic counter over the deduped stream in transcript
  order, chronological across both lanes; the lanes are separated at read time by
  `is_sidechain`, which is what keeps them from interleaving without losing ordering.
* `at` is the line's `timestamp` when it is a string, else omitted.
* Bounded like everything else the hook posts: `MAX_CONTEXT_SAMPLES = 2000` (keep the
  **last** 2000 — the tail is the interesting end of the curve) and
  `MAX_SAMPLE_TOOLS = 20` names per sample.

### Backend — model + migration

| Path | Why |
|---|---|
| `backend/app/db/models/coding.py` | `CodingContextSample`: `id` (autoincrement pk), `session_id` (`String(200)`, FK `coding_sessions.id` `ondelete="CASCADE"`), `seq` (`Integer`), `message_id` (`String(200)`), `is_sidechain` (`Boolean`, no server default — same reasoning as `CodingEnvelope.parsed`), `at` (`UTCDateTime`), `total_tokens`/`output_tokens` (`BigInteger`), `delta_tokens` (`Integer`, nullable — null means "no predecessor in this lane", which is not a delta of 0), `tools` (`JSONColumn`, nullable). `__table_args__`: `UniqueConstraint("session_id", "message_id", name="uq_coding_context_samples_session_message")` and `Index("ix_coding_context_samples_session_seq", "session_id", "seq")` — the read is exactly that pair. **The module docstring must name this table alongside `coding_envelopes` and `coding_gate_checks` as reported, not derived** (already done in the tree; keep it). |
| `backend/alembic/versions/0023_coding_context_samples.py` | `revision = "0023_coding_context_samples"`, `down_revision = "0022_session_launch_run_id"` (the current head — `0023` is the only file above `0022` in `backend/alembic/versions/`). `op.create_table` mirroring the model, then `op.create_index`; `downgrade()` drops index then table. Additive, reversible, no backfill — nothing in the stored event stream can reconstruct a sample. `JSONColumn` is passed as the column type exactly as `0017`/`0018` do. |

### Backend — ingest and read

| Path | Why |
|---|---|
| `backend/app/api/v1/coding/schemas.py` | `ContextSampleIn` (inbound: `seq`, `message_id`, `at: datetime \| None`, `total_tokens`, `output_tokens \| None`, `model \| None`, `is_sidechain: bool = False`, `tools: list[str] \| None`) plus `context_samples: list[ContextSampleIn] \| None` on `HookEventRequest` — optional, so the existing `_drop_unusable_optionals` validator drops a malformed list rather than 422'ing the hook. Read side: `ContextSample`, `ContextToolCost`, `ContextSeries`. |
| `backend/app/api/v1/coding/service.py` | `_record_context_samples(db, session_id, samples, now)` called from `_apply()` **only** — never `_apply_derived()`, which is the path a backfill replays. Upsert by `(session_id, message_id)`, clip `message_id` to 200 chars and drop an empty one, fall back to `now` when `at` is absent, cap the list and each `tools` list — the same truncate-never-reject posture as every other hook-fed field. Then recompute `delta_tokens` for the whole session in one pass: per lane, in `(seq, id)` order, `delta = total - previous.total`, first of a lane `None`. Recomputing wholesale is what makes a re-post of the same cumulative list idempotent and self-healing. `get_context_series(db, session_id)` for the read, 404 via `get_session_or_404`. |
| `backend/app/api/v1/coding/service.py` — `backfill_session` | **No `clear_*` call for this table**, and a docstring sentence saying so beside the existing sentence about reported evidence. This is the "survives backfill" requirement; the integration test pins it. |
| `backend/app/repositories/coding.py` | `get_context_sample`, `add_context_sample`, `context_samples_for_session` (all samples, ordered `seq, id`). Routes never touch the DB. |
| `backend/app/api/v1/coding/serializers.py` | `context_sample_to_schema`, `_context_tool_rollup`, `context_series_to_schema` — the header maths (baseline, peak, roll-up, exact split of a sample's delta across its tools) lives here beside the other read-time derivations, so nothing computable is stored. |
| `backend/app/api/v1/coding/routes.py` | `GET /coding-sessions/{session_id}/context`, `operation_id="readSessionContextSeries"`, `response_model=schemas.ContextSeries`, placed after `/events`. The route calls `service.get_context_series` and does nothing else. |

### Frontend

| Path | Why |
|---|---|
| `frontend/src/features/sessions/queries.ts` | `sessionContextQueryAtom = atomFamily(...)` over `atomWithQuery`, calling `api.coding.readSessionContextSeries(sessionId)`, with the same cache-driven `refetchInterval` the events atom uses so a finished run stops polling. |
| `frontend/src/features/sessions/components/ContextGrowthPanel.tsx` | The panel. Left: hand-rolled SVG area+line of `total_tokens` over `seq` — there is no charting dependency in this repo and none is added (`RunWaterfall`/`MiniLaneChart` set the precedent). Truncation samples get a marked drop with a `<title>` naming the size. Right: the ranked roll-up as `type="button"` rows (tool chip, summed delta, call count) calling `onSelectTool(name)`. `Skeleton` while pending, inline `AlertTriangle` + Retry on error, and **an empty series renders nothing at all** — every session recorded before this shipped has no samples. |
| `frontend/src/features/sessions/components/SessionDetailPage.tsx` | Mounts `<ContextGrowthPanel>`, owns the controlled tab state and `toolFilter`, renders the dismissible `ToolFilterChip`, and passes `toolName` down to `EventTimeline`. |
| `frontend/src/features/sessions/components/EventTimeline.tsx` | Optional `toolName?: string`, filtering the same cached stream `phaseId` already filters (`event.tool_name === toolName`), empty-state copy extended. No extra request. |

### Contract

| Path | Why |
|---|---|
| `frontend/openapi.json` | Hand-extended (no shell for the generator, as in v1.26/v1.27): the `/api/v1/coding-sessions/{session_id}/context` path object modelled on `/events`, the `ContextSample`/`ContextToolCost`/`ContextSeries`/`ContextSampleIn` schemas, and `context_samples` on `HookEventRequest`. |
| `frontend/src/api/generated/api.ts` | Hand-extended: four call sites (`CodingApiAxiosParamCreator`, `CodingApiFp`, `CodingApiFactory`, the `CodingApi` method) matching `listCodingSessionEvents`, plus the new interfaces and the `HookEventRequest.context_samples` field. |
| `docs/API_CONTRACT.md` | The section is **v1.29 — context-growth series**, already in the file at line 2535. v1.28 (folder picker home shortcut) and v1.30 (factory runs list) are taken; do not renumber. |

## Data / contract impact

**Database.** One new table, additive, nothing altered:

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

Alembic head moves `0022_session_launch_run_id` → `0023_coding_context_samples`. No
data migration: nothing in the stored event stream can reconstruct a sample, which is
precisely why the table is reported rather than derived.

**API — additive only.** No existing response changes shape.

```
ContextSample {
  seq, message_id, at
  total_tokens: int
  output_tokens: int | null
  delta_tokens: int | null        // null for the first sample of its lane
  is_truncation: bool             // derived: delta_tokens is not null and < 0
  tools: str[]
}
ContextToolCost { tool: str, delta_tokens: int, calls: int }
ContextSeries {
  session_id: str
  baseline_tokens: int | null     // first main-lane sample's total
  peak_tokens: int | null
  samples: ContextSample[]            // main lane, ordered by seq
  sidechain_samples: ContextSample[]  // subagent lane(s), their own series
  tools: ContextToolCost[]            // summed delta descending, then tool name
}
```

Header fields describe the **main lane only** — folding a subagent's totals into one
baseline would be a number nobody can point at. An empty series is `200` with empty
arrays and null baseline/peak; `404` is only for a session that does not exist.

**Roll-up attribution.** A sample's positive delta splits across the tools named on
it: `delta // n` each, remainder to the first tools in order, so the parts sum back to
the delta exactly. A sample with no tools contributes nothing, and negative deltas are
excluded entirely — a truncation is not something a tool earned.

**Hook body.** `HookEventRequest` gains one optional key, posted only on
Stop/SessionEnd and only when the transcript read produced samples.

## Test strategy

**Unit — `backend/tests/unit/test_hook_forwarder.py`** (extended; it already owns the
transcript tests and the line helpers). One test each:

1. **Message-id dedupe** — the same id on two lines (text, then tool call) yields one
   sample and the tool named on the second line is still resolved. Dedupe gates sample
   creation, never tool collection.
2. **Sidechain turns** — mixed `isSidechain` lines are flagged correctly and a
   sidechain `tool_result` never lands in the next main-lane sample's `tools`.
3. **A negative delta survives the read** — totals that step down are reported
   verbatim; the forwarder never clamps, the sign is the signal.
4. **Several `tool_result`s in one turn** — three blocks between two assistant
   messages produce a three-name list in call order, and an unresolvable
   `tool_use_id` is dropped rather than guessed.
5. **An assistant message with no usage block** — emits no sample and does not swallow
   the pending tool names.
6. Posture re-asserted: a missing transcript yields `[]`, and `build_body()` for a Stop
   with an unreadable transcript carries **no** `context_samples` key.

**Integration — `backend/tests/integration/test_coding_context.py`** (same shape as
`test_coding_evidence.py`: real test DB, ingest through `/api/v1/hooks/events`, reads
through the API):

* Samples come back in seq order with `delta_tokens` computed, `baseline_tokens` = the
  first sample's total, `peak_tokens` = the max.
* **The empty case** — a session with events but no samples: 200, empty arrays, null
  baseline/peak. An unknown session id: 404.
* **Survival across `backfill_session`** — ingest, POST the per-session backfill, read
  again: identical. The regression that matters most, and the one a future
  `clear_derived` edit would break.
* Re-posting the same cumulative list is idempotent — one row per message id.
* The roll-up ranks by summed positive delta, splits a multi-tool sample exactly, and
  excludes the truncation sample.
* Sidechain samples appear only in `sidechain_samples` and do not move the baseline.

**Component — `frontend/tests/components/contextGrowth.ct.tsx`**: mount
`SessionDetailPage` with the route mock extended for `/context`; assert a point per
sample, the truncation marker present and labelled, the roll-up ranked highest-delta
first, and that clicking a tool row switches to the events tab filtered to that tool.

**The other CT specs must keep their `/context` branch.**
`frontend/tests/components/sessionDetailPage.ct.tsx` and
`frontend/tests/components/phaseEvidence.ct.tsx` fulfil `**/api/v1/**` with "the run
object unless the path ends in `/events`" — without a `/context` branch a series
request is answered with a `CodingSessionDetail` and the panel fails to render. The
shared `contextSeries()` fixture lives in
`frontend/tests/components/harness/runFixtures.ts` beside `factoryRun()`.

**Done means** `ruff check`, `ruff format --check`, `mypy app` and `pytest` green in
`backend/`, and `pnpm typecheck` + `pnpm test:ct` green in `frontend/`.

## Risks

1. **Formatting on the new migration.** `0023_coding_context_samples.py`'s
   `op.drop_index(...)` is split without a magic trailing comma and joins to ~96 chars
   under a 100-char limit — the most likely single red gate. Check it first.
2. **Generated-client drift.** `openapi.json` and `api.ts` are hand-extended, so a
   later real generator run reorders them. Mitigation: copy the surrounding style
   exactly, so a regeneration is a no-op diff. Same risk taken knowingly in v1.26/v1.27.
3. **The two existing CT mocks.** Highest-probability frontend failure, and it fails as
   a confusing render error rather than a clean 404.
4. **The dedupe/tool-map interaction in the forwarder.** Collecting `tool_use` blocks
   only from first-seen message ids would silently produce empty `tools[]` for most
   samples — a curve with an empty roll-up beside it, looking like a backend bug.
   Unit test 1 exists for exactly this.
5. **The forwarder must stay stdlib-only and fail-silent.** It runs under a bare
   `python3` outside the app; any non-stdlib import, or any exception that escapes,
   breaks real coding sessions. Everything new stays inside the existing `try`, and
   `main()`'s blanket `except Exception: pass` stays the last line of defence.
6. **A backfill deleting the new table.** `clear_derived` is the natural place a future
   contributor would add it. The model docstring, the `backfill_session` docstring and
   the integration test are the three places this is written down.
7. **Delta recomputation cost** — O(samples) per hook firing on the hook's critical
   path, bounded by the 2000-sample cap and a single indexed read.
8. **`is_sidechain` absent on older transcripts** makes every sample main-lane. That is
   the honest reading and degrades to today's behaviour rather than misattributing.
9. **Even-split attribution is a modelling choice, not a measurement.** When four tool
   results land in one turn nobody knows which cost what; the split is documented in
   the contract so the number is not read as more precise than it is.
10. **A truncation is inferred from a negative delta**, and a resumed transcript could
    produce one that is not a compaction. It is still a real drop in the window, which
    is what the marker claims — the copy says "context truncated", not "compaction ran".
11. **One more polling query per open session detail page**, mitigated by stopping the
    poll once the run has ended, as the sibling queries do.

## Assumptions

Each is a question I would have asked; none is destructive, so the run continues.

1. **The contract section is v1.29, not the requested v1.28.** `docs/API_CONTRACT.md`
   already had a v1.28 (folder picker home shortcut) before this work, and v1.30
   (factory runs list) has since landed on top. The context-growth section is v1.29 and
   stays there; renumbering would break two neighbours.
2. **The route is `/api/v1/coding-sessions/{session_id}/context`**, not the literally
   requested `/api/v1/coding/sessions/{id}/context`. Every other coding route has the
   `/api/v1/coding-sessions/{session_id}/…` shape; the requested form would be the only
   route of its kind. `operation_id` is `readSessionContextSeries` exactly as asked, so
   the frontend call site is unaffected either way.
3. **`model` rides the hook body but is not persisted** — the specified column list has
   no `model` column, so ingest accepts and ignores it rather than inventing one.
4. **`seq` is one monotonic counter over the deduped stream**, chronological across
   both lanes, with separation by `is_sidechain` at read time. A per-lane counter would
   also satisfy "never interleaved" but would lose the relative ordering between a
   subagent's turns and the main lane's.
5. **Header fields describe the main lane only**; sidechain samples get their own array.
6. **The roll-up splits a multi-tool sample's delta evenly** (exact integer split,
   remainder to the first tools) rather than charging the full delta to each tool.
7. **An empty series renders no panel at all**, rather than an empty state — every
   session recorded before this shipped has no samples.
8. **`delta_tokens` is null, not 0, for the first sample of a lane** — "no predecessor"
   and "no growth" are different facts.
9. **The forwarder caps at the last 2000 samples and 20 tool names per sample**, the
   same truncate-rather-than-fail posture as the rest of the hook payload.
10. **`BackfillResult`/`BackfillTotals` are unchanged** — the table is not something a
    backfill produces, and both schemas are part of the frozen contract.
11. **The build stage verifies rather than re-creates.** The implementation is already
    in this checkout (see "State of the working tree"); re-authoring it from scratch
    would churn files the request did not ask to change and risk renumbering the
    migration and the contract section.
