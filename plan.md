# Context-growth series for coding sessions

## The change, in one paragraph

Claude Code's transcript already tells us how the context window filled up — every
assistant message carries a cumulative `usage` block — and the forwarder throws all
of it away except the sum. This change keeps the shape of that curve. A new
`context_samples()` in `backend/app/observability/forwarders/claude_code.py` walks
the same transcript the existing `transcript_usage()` walks, dedupes by assistant
message id exactly as it does, and returns one ordered sample per API response:
`{seq, message_id, at, total_tokens, output_tokens, model, is_sidechain, tools[]}`,
where `tools[]` names the tool results that landed in context since the previous
assistant message, resolved by `tool_use_id`. The list rides the existing
Stop/SessionEnd hook body under a new `context_samples` key. The backend stores it
in a new **reported** (not derived) table `coding_context_samples`, upserted by
`(session_id, message_id)`, with a per-lane `delta_tokens` recomputed on every
ingest; a new `GET /api/v1/coding-sessions/{session_id}/context`
(`readSessionContextSeries`) returns the main lane's ordered samples, the sidechain
samples as their own series, and a header carrying `baseline_tokens`, `peak_tokens`
and a per-tool roll-up of summed positive deltas. Negative deltas — a real context
truncation, observed as a −7570 step — are stored, flagged, and kept out of the
roll-up. The frontend adds a `ContextGrowthPanel` to the session detail page: an
area chart of `total_tokens` over `seq` with truncation drops marked, and beside it
the ranked tool roll-up whose rows filter the existing `EventTimeline` to that tool.

## Files to add or change

### Forwarder (stdlib-only, fail-silent — keep it that way)

| Path | Why |
|---|---|
| `backend/app/observability/forwarders/claude_code.py` | **Change.** Add `context_samples(path) -> list[dict]` beside `transcript_usage()`, and post it in `build_body()` under `body["context_samples"]` for the same `event in ("Stop", "SessionEnd") and raw.get("transcript_path")` branch that already sets `body["stats"]`. Key added only when the list is non-empty, so an unreadable transcript leaves the autonomous hook payload byte-for-byte as it is today. No new import beyond what the module already has (`json`, `os`, `pathlib`); the file stays runnable under a bare `python3`. |

`context_samples()` in detail (this is the part with the traps):

* One pass over the JSONL, same `try/except ValueError` per line for the half-written
  tail and the same `except OSError: return []` for a missing file.
* **Two maps, one walk.** `tool_use_id -> tool name` is collected from *every*
  assistant line's `message.content` blocks of `type == "tool_use"` — **not** only
  from first-seen message ids. One API response spans several lines (text on one,
  the `tool_use` on the next) carrying the same `message.id`, so the message-id
  dedupe that keeps usage from doubling must not also swallow the tool names.
* Pending tool names accumulate from `type == "user"` lines whose
  `message.content` holds `tool_result` blocks; each block's `tool_use_id` is looked
  up in the map and appended by name. An id that resolves to nothing is skipped
  (never invented, never posted as a raw id).
* A sample is emitted when an assistant line carries a `dict` `usage` and a `str`
  `message.id` not yet seen. It takes the pending tool names, then clears them.
  `total_tokens = input_tokens + cache_read_input_tokens + cache_creation_input_tokens`
  (each `int(... or 0)`, as `transcript_usage` already does), `output_tokens` from
  the same block. An assistant message with no `usage` block emits no sample and
  does not clear the pending list.
* **Lanes are kept apart at the source**: `is_sidechain = bool(record.get("isSidechain"))`,
  and the pending-tools accumulator is keyed by that flag, so a subagent's
  `tool_result` can never be attributed to the main lane's next message.
* `seq` is a single monotonic counter over the deduped stream in transcript order
  (1-based). It stays chronological across both lanes; the lanes are separated at
  read time by `is_sidechain`, which is what keeps them from interleaving without
  losing the ordering.
* `at` is the line's `timestamp` string when it is one, else omitted.
* Bounded like everything else the hook posts: cap the list at `MAX_CONTEXT_SAMPLES = 2000`
  (keep the **last** 2000, since the tail is the interesting end of the curve) and
  `MAX_SAMPLE_TOOLS = 20` names per sample.

### Backend — model + migration

| Path | Why |
|---|---|
| `backend/app/db/models/coding.py` | **Change.** Add `CodingContextSample`. Columns exactly as specified: `id` (autoincrement pk, like every other child table here), `session_id` (`String(200)`, FK `coding_sessions.id` `ondelete="CASCADE"`), `seq` (`Integer`), `message_id` (`String(200)`), `is_sidechain` (`Boolean`, no server default — same reasoning as `CodingEnvelope.parsed`), `at` (`UTCDateTime`), `total_tokens` / `output_tokens` (`BigInteger`), `delta_tokens` (`Integer`, nullable — null means "no predecessor in this lane", which is not the same as a delta of 0), `tools` (`JSONColumn`, nullable). `__table_args__`: `UniqueConstraint("session_id", "message_id", name="uq_coding_context_samples_session_message")` plus `Index("ix_coding_context_samples_session_seq", "session_id", "seq")` — the read is exactly that pair. **And the module docstring at the top of the file must be updated**: it currently says `coding_envelopes` and `coding_gate_checks` are "the exception — they are *reported*". This table joins that list, for the same reason (the hook body is not stored, so a replay cannot rebuild it). |
| `backend/alembic/versions/0023_coding_context_samples.py` | **Add.** `revision = "0023_coding_context_samples"`, `down_revision = "0022_session_launch_run_id"` (the current head — confirmed against `backend/alembic/versions/` and the v1.28 contract note). `op.create_table` mirroring the model, then `op.create_index` for the seq index; `downgrade()` drops index then table. Additive, reversible, no backfill (nothing can be reconstructed from the stored event stream). |

### Backend — ingest

| Path | Why |
|---|---|
| `backend/app/api/v1/coding/schemas.py` | **Change.** Add `ContextSampleIn` (the hook's inbound shape: `seq: int`, `message_id: str`, `at: datetime \| None`, `total_tokens: int`, `output_tokens: int \| None`, `model: str \| None`, `is_sidechain: bool = False`, `tools: list[str] \| None`) and add `context_samples: list[ContextSampleIn] \| None` to `HookEventRequest`. Optional, so the existing `_drop_unusable_optionals` validator already drops a malformed list rather than 422'ing the hook. Add the read schemas: `ContextSample`, `ContextToolCost`, `ContextSeries` (below). |
| `backend/app/api/v1/coding/service.py` | **Change.** Add `_record_context_samples(db, session_id, samples, now)` called from `_apply()` **only** — never from `_apply_derived()`, which is the code path a backfill replays. Upsert each sample by `(session_id, message_id)` (update `seq`, `at`, `total_tokens`, `output_tokens`, `is_sidechain`, `tools`; insert when absent), clip `message_id` to 200 chars and drop a sample with an empty one, fall back to `now` when `at` is absent/unparseable, cap the accepted list at `MAX_CONTEXT_SAMPLES = 2000` and each `tools` list at 20 names of ≤ 200 chars — the same truncate-never-reject posture as every other hook-fed field. Then recompute `delta_tokens` for the whole session in one pass: per lane, ordered by `(seq, id)`, `delta = total_tokens - previous.total_tokens`, first of a lane `None`. Recomputing wholesale rather than incrementally is what makes a re-post of the same cumulative list idempotent and self-healing. Add `get_context_series(db, session_id)` for the read side (404 via the existing `get_session_or_404`). |
| `backend/app/api/v1/coding/service.py` — `backfill_session` | **Change (one line of intent, no deletion).** `coding_context_samples` must **not** be cleared: no `clear_*` call is added for it, and the docstring gains a sentence saying so, next to the existing sentence about reported evidence. This is the "survives across backfill" requirement, and the test below pins it. |
| `backend/app/repositories/coding.py` | **Change.** `get_context_sample(db, session_id, message_id)`, `add_context_sample(...)`, `context_samples_for_session(db, session_id)` (all samples, ordered `seq, id`), and `context_sample_count(db, session_id)` if the backfill result ever wants it (**not** added to `BackfillResult` — that dataclass and its schema are part of the frozen contract and the table is not something a backfill produces). |
| `backend/app/api/v1/coding/serializers.py` | **Change.** `context_sample_to_schema(row)` and `context_series_to_schema(samples, session_id)` — the header maths (baseline, peak, roll-up, even split of a sample's delta across its tools) lives here, beside the other derived-at-read-time fields, so nothing is stored that can be computed. |
| `backend/app/api/v1/coding/routes.py` | **Change.** `GET /coding-sessions/{session_id}/context`, `operation_id="readSessionContextSeries"`, `response_model=schemas.ContextSeries`, `tags` inherited from the existing `router`. Placed after the `/events` route. Route does nothing but call `service.get_context_series`. |

### Frontend

| Path | Why |
|---|---|
| `frontend/src/features/sessions/queries.ts` | **Change.** Add `sessionContextQueryAtom = atomFamily((sessionId) => atomWithQuery(...))` calling `api.coding.readSessionContextSeries(sessionId)`, `enabled: sessionId.length > 0`, and the same cache-driven `refetchInterval` `codingSessionEventsQueryAtom` uses (read the session out of the query cache; stop polling once `ended_at` is set) so a closed run stops asking. |
| `frontend/src/features/sessions/components/ContextGrowthPanel.tsx` | **Add.** The panel. Left: a hand-rolled SVG area+line of `total_tokens` over `seq` — there is no charting dependency in this repo and none is being added (`RunWaterfall`/`MiniLaneChart` set the precedent of computing geometry and drawing with plain elements). Truncation samples (`is_truncation`) get a marked drop: a vertical rule plus a dot, with a `<title>`/tooltip naming the size of the drop. Right: the ranked tool roll-up as a list of `type="button"` rows (per the house rule from commit 430182e), each showing tool name, summed delta and call count, calling `onSelectTool(name)`. States: `Skeleton` while pending and the inline `AlertTriangle` + `Retry` block, both copied in shape from `FolderPickerDialog`'s body, which is the pattern the request points at. An empty series renders **nothing at all** — every session recorded before this ships has no samples, and an empty box on all of them is noise. |
| `frontend/src/features/sessions/components/SessionDetailPage.tsx` | **Change.** Mount `<ContextGrowthPanel>` between `<ChildRuns>` and `<RunViews>`. Lift two pieces of state into the page: `tab` (the `Tabs` become controlled — `value`/`onValueChange` instead of `defaultValue`) and `toolFilter: string \| null`. Selecting a tool row sets the filter and switches to the `events` tab; `RunViews` renders a dismissible "filtered to `<tool>`" chip above the timeline and passes `toolName` down. |
| `frontend/src/features/sessions/components/EventTimeline.tsx` | **Change.** New optional `toolName?: string` prop, filtering the same cached stream the existing `phaseId` prop filters — `event.tool_name === toolName` — with the empty-state copy extended for "no events for this tool". No extra request, exactly like `phaseId`. |

### Contract

| Path | Why |
|---|---|
| `frontend/openapi.json` | **Change, by hand** (no shell for the generator, as in v1.26/v1.27). Add the `/api/v1/coding-sessions/{session_id}/context` path object next to `/events`, modelled on it (path param + 200 + 422), and the `ContextSample`, `ContextToolCost`, `ContextSeries`, `ContextSampleIn` schemas under `components.schemas`; add `context_samples` to `HookEventRequest`'s properties. |
| `frontend/src/api/generated/api.ts` | **Change, by hand.** Four call sites per operation, matching `listCodingSessionEvents`: `CodingApiAxiosParamCreator`, `CodingApiFp`, `CodingApiFactory`, and the `CodingApi` class method — plus the `ContextSample` / `ContextToolCost` / `ContextSeries` interfaces and the new `HookEventRequest.context_samples` field in the model section. |
| `docs/API_CONTRACT.md` | **Change.** **v1.29**, not v1.28 — v1.28 (folder picker home shortcut) is already in the file at line 2506. See assumptions. New section documents the endpoint, the three schemas, the reported-not-derived rule, the negative-delta semantics, the lane separation, and the roll-up's attribution rule. |

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
  output_tokens  bigint
  delta_tokens   int null      -- null = first sample of its lane
  tools          json null
  unique (session_id, message_id)
  index (session_id, seq)
```

Alembic head moves `0022_session_launch_run_id` → `0023_coding_context_samples`. No
data migration: nothing in the stored event stream can reconstruct a sample, which
is precisely why the table is reported rather than derived.

**API — additive only.** No existing response changes shape.

```
ContextSample {
  seq: int
  message_id: str
  at: datetime
  total_tokens: int
  output_tokens: int | null
  delta_tokens: int | null        // null for the first sample of its lane
  is_truncation: bool             // derived: delta_tokens is not null and < 0
  tools: str[]                    // tool results that landed since the previous sample
}
ContextToolCost {
  tool: str
  delta_tokens: int               // summed POSITIVE delta attributed to it
  calls: int                      // samples this tool appeared in
}
ContextSeries {
  session_id: str
  baseline_tokens: int | null     // first main-lane sample's total — the static
                                  // preamble plus the first user message
  peak_tokens: int | null
  samples: ContextSample[]        // main lane, ordered by seq
  sidechain_samples: ContextSample[]  // subagent lane(s), their own series
  tools: ContextToolCost[]        // summed delta descending, then tool name
}
```

Header fields (`baseline_tokens`, `peak_tokens`, `tools`) describe the **main lane
only** — the sidechain samples are a separate series and folding them into one
baseline would be a number nobody can point at. An empty series returns
`200` with empty arrays and null baseline/peak, never a 404 for a session that
exists (404 is only for an unknown session, via `get_session_or_404`).

**Roll-up attribution.** A sample's positive delta is split across the tools named
on that sample: `base = delta // n` to each, the `delta % n` remainder handed to the
first tools in order, so the parts sum back to the delta exactly and the roll-up
never claims more growth than happened. A sample with no tools (a plain text turn, a
user prompt) contributes to neither the roll-up nor any tool's `calls`. Negative
deltas are excluded entirely — a truncation is not something a tool earned.

**Hook body.** `HookEventRequest` gains one optional key. The forwarder posts it
only on Stop/SessionEnd, only when the transcript read produced samples.

## Test strategy

**Unit — `backend/tests/unit/test_hook_forwarder.py`** (extend; it already owns the
transcript-reading tests and the `_assistant()` line helper). Add a small fixture
builder for a transcript JSONL with assistant / user / tool_use / tool_result lines,
and cover, one test each:

1. **Message-id dedupe** — the same message id on two lines (text, then tool call)
   yields exactly one sample, and the tool named on the second line is still
   resolved. This is the trap: dedupe must gate sample creation, not tool collection.
2. **Sidechain turns** — a transcript mixing `isSidechain: true` and main lines
   produces samples flagged correctly, and a sidechain `tool_result` never lands in
   the next main-lane sample's `tools`.
3. **A negative delta survives the read** — totals that step down mid-transcript are
   reported verbatim (the forwarder never clamps; the sign is the signal).
4. **Several tool_results resolved in one turn** — three `tool_result` blocks
   between two assistant messages produce a three-name `tools` list in call order,
   and an unresolvable `tool_use_id` is dropped rather than guessed.
5. **An assistant message with no usage block** — emits no sample, and does not
   swallow the pending tool names (they attach to the next real sample).
6. Existing posture, re-asserted: a missing transcript yields `[]`, and
   `build_body()` for a Stop with an unreadable transcript carries **no**
   `context_samples` key (the autonomous payload is unchanged).

**Integration — `backend/tests/integration/test_coding_context.py`** (new file,
same shape as `test_coding_evidence.py`: real test DB, `_ingest` posts to
`/api/v1/hooks/events`, reads go through the API):

* Ingesting a Stop with `context_samples` and reading
  `GET /api/v1/coding-sessions/{id}/context` returns the samples in seq order with
  `delta_tokens` computed, `baseline_tokens` = the first sample's total, and
  `peak_tokens` = the max.
* **The empty case** — a session with events but no samples returns 200 with empty
  arrays and null baseline/peak; an unknown session id returns 404.
* **Survival across `backfill_session`** — ingest samples, POST
  `/api/v1/coding-sessions/{id}/backfill`, read the series again: byte-identical.
  This is the regression that matters most, and it is the one a future
  `clear_derived` edit would break.
* Re-posting the same (cumulative) list is idempotent: one row per message id, same
  deltas.
* The roll-up ranks by summed positive delta, splits a multi-tool sample's delta
  exactly, and excludes the truncation sample.
* Sidechain samples come back in `sidechain_samples` and never in `samples`, and do
  not move `baseline_tokens`.

**Component — `frontend/tests/components/contextGrowth.ct.tsx`** (new): mount
`SessionDetailPage` with the route mock extended for `/context`; assert the chart
renders a point per sample, the truncation marker is present and labelled, the tool
list is ranked highest-delta-first, and clicking a tool row switches to the All
events tab with the stream filtered to that tool.

**Two existing CT specs must be updated or they break**:
`frontend/tests/components/sessionDetailPage.ct.tsx` and
`frontend/tests/components/phaseEvidence.ct.tsx` both fulfil `**/api/v1/**` with
"the run object unless the path ends in `/events`" — a `/context` request would be
answered with a `CodingSessionDetail`. Both mocks need a `/context` branch returning
an empty (or fixture) series. A shared `contextSeries()` fixture goes in
`frontend/tests/components/harness/runFixtures.ts` beside `factoryRun()`.

**Done means**: `uv run ruff check`, `uv run ruff format --check`, `uv run mypy app`,
`uv run pytest` all green in `backend/`, and `pnpm test:ct` green in `frontend/`
(plus `pnpm typecheck`, which the hand-edited client has to satisfy).

## Risks

1. **Generated-client drift.** `openapi.json` and `api.ts` are hand-extended, so a
   later `pnpm generate:api` will reformat or reorder them. Mitigation: copy the
   surrounding style exactly (four call sites, same parameter ordering, same JSDoc
   shape) so a regeneration is a no-op diff. Same risk taken knowingly in v1.26/v1.27.
2. **Breaking the two existing CT mocks** (above). Highest-probability failure in the
   whole change, and it fails as a confusing render error, not a clean 404.
3. **The dedupe/tool-map interaction in the forwarder.** Collecting `tool_use` blocks
   only from first-seen message ids would silently produce empty `tools[]` for most
   samples — the panel would render a curve with an empty roll-up beside it and look
   like a backend bug. Test 1 exists for exactly this.
4. **The forwarder must stay stdlib-only and fail-silent.** It runs under a bare
   `python3` outside the app; any import beyond the stdlib, or any exception that
   escapes, breaks real coding sessions. Everything new goes inside the existing
   `try`, and `main()`'s blanket `except Exception: pass` stays the last line of
   defence.
5. **A backfill deleting the new table.** `clear_derived` is the natural place a
   future contributor would add it. The model docstring, the `backfill_session`
   docstring and the integration test are the three places this is written down.
6. **Delta recomputation cost.** Recomputing every delta on every Stop is O(samples)
   per hook firing, on the critical path of a hook. Bounded by the 2000-sample cap
   and a single indexed read; still worth watching on a very long session.
7. **`is_sidechain` may be absent on older transcripts**, making every sample
   main-lane. That is the honest reading (nothing said otherwise) and degrades to
   today's behaviour rather than misattributing.
8. **Even-split attribution is a modelling choice, not a measurement.** When four
   tool results land in one turn, nobody knows which of them cost what. The split is
   documented in the contract so the number is not read as more precise than it is.
9. **A truncation is inferred from a negative delta**, and a transcript that
   restarts (`--resume` into the same file) could produce one that is not a
   truncation. It is still a real drop in the context window, which is what the
   marker claims; the copy says "context dropped", not "compaction ran".
10. **Session-detail page weight.** One more polling query per open session detail
    page. Mitigated by stopping the poll on `ended_at`, as the sibling queries do.
11. **`model` is posted but not stored** (see assumptions) — a reviewer may read the
    forwarder's richer sample as a lost field rather than a deliberate one.

## Assumptions

Each of these is a question I would have asked; none is destructive, so the run
continues under them.

1. **The contract section is v1.29, not v1.28.** `docs/API_CONTRACT.md` already has
   a v1.28 ("folder picker home shortcut", line 2506) from the commit before this
   one. v1.29 is the next free number.
2. **The route is `/api/v1/coding-sessions/{session_id}/context`**, not the
   literally-requested `/api/v1/coding/sessions/{id}/context`. Every other coding
   route in this API is `/api/v1/coding-sessions/{session_id}/…`; the requested form
   would be the only route of its shape and a permanent inconsistency. The
   `operation_id` is `readSessionContextSeries` exactly as asked, so the frontend
   call site is unaffected either way.
3. **`model` rides the hook body but is not persisted.** The specified column list
   has no `model` column, so the ingest accepts and ignores it rather than inventing
   a column the request did not ask for.
4. **`seq` is a single monotonic counter over the deduped stream**, chronological
   across both lanes; separation is by `is_sidechain` at read time. The alternative
   (a per-lane counter) also satisfies "never interleaved" but loses the relative
   ordering between a subagent's turns and the main lane's.
5. **Header fields describe the main lane only.** Sidechain samples get their own
   array and are excluded from `baseline_tokens`, `peak_tokens` and the roll-up.
6. **The roll-up splits a multi-tool sample's delta evenly** (exact integer split,
   remainder to the first tools) rather than charging the full delta to each tool.
7. **An empty series renders no panel at all** on the detail page, rather than an
   empty state — every historical session has no samples.
8. **`delta_tokens` is null (not 0) for the first sample of a lane**, since "no
   predecessor" and "no growth" are different facts.
9. **The forwarder caps at the last 2000 samples and 20 tool names per sample**, the
   same truncate-rather-than-fail posture the rest of the hook payload takes.
10. **`BackfillResult` / `BackfillTotals` are unchanged.** The table is not something
    a backfill produces, and both schemas are part of the frozen contract.
