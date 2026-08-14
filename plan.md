# Plan — real interview mode for the in-app launcher

## The change, in one paragraph

`--interview` becomes a real mode of a factory run. A run started with it executes
the `plan` stage exactly as today, then — instead of continuing to `build` — turns
the plan envelope's `assumptions[]` into a list of questions, writes them to
`<run_dir>/questions.json`, marks `run.json` as `state: "waiting_input"`, and exits
0 without calling the builder. Resuming such a run (`--resume <run_id>`) requires
`<run_dir>/answers.json`: with it, each question+answer pair is folded into the
build stage's first user prompt alongside the plan, so the builder honours the
user's answers instead of the planner's guesses; without it, the resume refuses
with a message naming the file it wants and exits 2. The backend passes
`--interview` (and a server-generated `--run-id`) when `mode == "interview"`,
gains three endpoints — list the recent launches, read one launch's interview
state, submit its answers — and the answer endpoint writes `answers.json` and
spawns the same detached fire-and-forget resume subprocess through an injected
spawner. The Sessions screen polls the launches list and, for a run that is
waiting, renders one required text field per question with an "Answer and
continue" submit that posts the answers and confirms the run resumed. An
autonomous launch is untouched: same argv, same code path, no run id, no files.

## Run-id handling — the choice, and why

The task offers two ways for the backend to learn a launched run's id. **This plan
generates the run id server-side and passes it to `run.py` via a new `--run-id`
flag.** The factory already threads a caller-supplied run id all the way through
(`load_config(run_id=...)` in `factory/adw/config.py:416`, which is exactly how
`--resume` re-uses the recorded id), so the flag is a five-line addition to an
existing seam. The alternative — parsing the run id back out of the launch log
(`factory run <id> → <path>`, `run.py:505`) — is a race (the backend would have to
poll a log that may not exist yet) against a line that is presentation, not
contract. `--run-id` is deterministic: the launch row holds the id before the
child is even spawned.

Only interview launches get a run id. An autonomous launch keeps `run_id = NULL`
and its argv byte-for-byte identical to today's, which is the stated done-criterion.

## The on-disk contract (new, cross-process)

Owned by a new module, `factory/adw/interview.py`, and mirrored — read-only for
questions, write-only for answers — by `backend/app/services/factory_runs.py`.

`<run_dir>/questions.json`, written by the factory:

```json
{
  "run_id": "a1b2c3d4",
  "stage": "plan",
  "asked_at": "2026-08-14T10:00:00+00:00",
  "questions": [{ "id": "q1", "question": "<assumption text, verbatim>" }]
}
```

`<run_dir>/answers.json`, written by the backend, read by the factory:

```json
{
  "answered_at": "2026-08-14T10:05:00+00:00",
  "answers": [{ "id": "q1", "question": "<verbatim>", "answer": "<user text>" }]
}
```

Ids are `q1..qN` by position. Both files are written atomically (tmp + `os.replace`),
the way `runs.write` already writes `run.json`.

`<run_dir>` is `<runs root>/<run_id>`, and the runs root is
`~/.masterwork/runs/<project dir name>` unless the target repo's
`factory.config.json` sets `"runs_dir"` (`factory/adw/config.py:259-274`). The
backend mirrors that rule rather than passing `--runs-dir`, so an interview run's
logs land exactly where every other run of that repo lands.

---

## Files to add or change

### Factory

**`factory/adw/interview.py` (new)** — the whole file contract in one module:
`QUESTIONS_FILENAME`/`ANSWERS_FILENAME`; frozen `Question(id, question)` and
`Answer(id, question, answer)`; `questions_from_assumptions(assumptions)` (drops
blank entries, numbers the rest `q1..qN`); `write_questions(run_dir, run_id, questions)`;
`read_questions(run_dir)`; `read_answers(run_dir)`, which raises `InterviewError`
when the file is missing, malformed, or does not answer exactly the recorded ids;
and `prompt_block(answers)` — the text folded into the build prompt. Module
docstring states both JSON shapes, because the backend writes one of them.

**`factory/adw/runs.py`** — a paused run has to survive the process that paused it:
add `WAITING_INPUT = "waiting_input"` beside `RUNNING`/`FINISHED`/`STOPPED`; add
`interview: bool = False` to `RunRecord` and to `RunRecord.FIELDS` (`from_dict`
already ignores unknown keys and defaults missing ones, so an older `run.json`
still reads); accept `interview` in `open_record`; add `pause_record(run_dir, *, reason)`
which clears the pid and sets `state=WAITING_INPUT` without touching `accepted`
(`close_record` forces an `accepted` verdict, and a paused run has none).
`live_state` already passes non-`running` states through untouched, and
`plan_resume` already allows a record that is neither running nor
finished-and-accepted — so resume works with no change there.

**`factory/adw/pipeline.py`** — `Pipeline.__init__` gains `interview: bool = False`
and `answers: list[interview.Answer] | None = None`. In `_run()`, after the `plan`
stage passes and commits (so the plan is in git before we stop), if `self.interview`
and no answers were supplied: build the questions from `outcome.envelope.assumptions`;
if the list is empty, emit a telemetry note and continue to build (nothing to ask —
see Assumptions); otherwise write `questions.json` and `return self._finish(reason, paused=True)`.
`_finish` gains `paused: bool = False`: it calls `runs.pause_record` instead of
`runs.close_record`, leaves `accepted` False, and emits `run_end` with
`result="ok"` (a pause is not a failure, and masterwork's Sessions screen reads
that field). `RunResult` gains `paused`, `questions`, `questions_path`, and
`exit_code` returns 0 when `paused`. `_agent_stage` appends
`interview.prompt_block(self.answers)` to the compiled **user** prompt when the
stage is `build` and answers are present — folding it into the prompt rather than
into a role template is deliberate: role files live in the user's seeded
`~/.masterwork/agents` library and a new `{{...}}` variable would never appear in
an already-seeded copy. `format_summary` gains an `interview_report(result)` block
naming the questions, the `answers.json` path to write, and the exact resume command.

**`factory/run.py`** —
* `--interview` (store_true) and `--run-id RUN_ID`.
* `--run-id` is validated as a path segment before it is ever joined to a path:
  non-empty, ≤64 chars, `[A-Za-z0-9._-]` only, not `.`/`..`; and refused if
  `<runs root>/<run_id>/run.json` already exists. Exit 2 with a stated reason.
* `resume_conflicts()` grows two entries: `--run-id` (a resume takes its id from
  the record) and `--interview` (interview-ness is read from the record).
* `--interview` on a workflow with no `plan` stage is refused at startup, exit 2.
* On `--resume`, when the record's state is `waiting_input`: read
  `<run_dir>/answers.json` through `interview.read_answers`; on `InterviewError`
  print it and exit 2 (naming the path and the outstanding question ids), never
  starting an agent. Otherwise pass the answers into the `Pipeline`.
* `interview=` for the pipeline is `args.interview` for a fresh run and
  `resume.record.interview` for a resumed one.

**`factory/tests/test_interview.py` (new)**, on the existing fake-CLI harness
(`factory/tests/conftest.py`, the style of `test_lifecycle.py`).

### Backend

**`backend/app/db/models/launcher.py`** — `run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)`,
with a one-line comment that only interview launches carry one.

**`backend/alembic/versions/0022_session_launch_run_id.py` (new)** — `down_revision = "0021_work_assignee_sprint"`
(the current single head; verified against `0021_work_assignee_and_current_sprint.py:16-17`).
`add_column`/`drop_column` only — additive and reversible.

**`backend/app/config.py`** — `factory_runs_root: Path = MASTERWORK_HOME / "runs"`,
so the run-dir root is configuration rather than a literal and tests can point it
at `tmp_path`.

**`backend/app/services/factory_runs.py` (new)** — the backend half of the file
contract, no FastAPI in it: `new_run_id()` (`secrets.token_hex(4)`, the same shape
`factory/adw/config.new_run_id` produces), `runs_root_for(project_path)` (mirrors
the factory's rule: `factory.config.json` `"runs_dir"` when present — expanded,
relative resolved against the project — else `settings.factory_runs_root / project.name`),
`run_dir_for(project_path, run_id)`, `read_questions(run_dir)`,
`answers_exist(run_dir)`, `read_run_state(run_dir)` (the `state` field of
`run.json`, `None` when absent or unreadable), and `write_answers(run_dir, pairs)`
(atomic tmp + `os.replace`, refusing to create the run dir — a run dir that does
not exist means the run never started, and inventing one would strand the answers).

**`backend/app/services/factory_launcher.py`** — `spawn_factory_run` gains
`run_id: str | None = None` and `interview: bool = False`; argv appends
`--run-id <id>` and `--interview` **only** when they are set, so the autonomous
argv is unchanged to the byte. New `spawn_factory_resume(*, repo_root, python_bin,
project_path, run_id, log_path)` → `[python, <repo>/factory/run.py, --repo,
<project>, --resume, <run_id>]`. Both go through one private `_spawn(argv, project_path, log_path)`
holding the existing `Popen` options (`start_new_session=True`, `stdin=DEVNULL`,
log appended, argv list never a shell string) and the `_reap()` bookkeeping.

**`backend/app/api/deps.py`** — `LaunchSpawner` becomes a keyword-only `Protocol`
(`Protocol` is already the repo's idiom — `app/providers/base.py:74`,
`app/observability/base.py:49`) with `__call__(*, project_path, request_text,
log_path, run_id, interview) -> int`; new `ResumeSpawner` Protocol
(`__call__(*, project_path, run_id, log_path) -> int`) and `get_resume_spawner`,
bound to `settings.masterwork_repo_root` / `settings.factory_python` the same way
`get_launch_spawner` already is. Both stay overridable so no test ever forks.

**`backend/app/repositories/launcher.py`** — `create_launch(..., run_id: str | None = None)`;
new `get_launch(db, launch_id)` and `list_launches(db, limit=20)` (newest first by
`launched_at`, `id` as tiebreak). Routes never touch the session directly.

**`backend/app/api/v1/launcher/schemas.py`** —
```
InterviewState = "not_interview" | "starting" | "running" | "waiting" | "answered" | "finished"   (StrEnum)
InterviewQuestion { id: str, question: str }
InterviewRead { launch_id: int, run_id: str | null, state: InterviewState,
                run_state: str | null, questions: InterviewQuestion[] }
InterviewAnswer { id: str, answer: str (min_length 1) }
InterviewAnswersRequest { answers: InterviewAnswer[] (min_length 1) }
InterviewResumeRead { launch_id: int, run_id: str, resumed: bool, pid: int | null }
SessionLaunchRead: + run_id: str | null
SessionLaunchListItem: SessionLaunchRead + interview: InterviewRead | null
```

**`backend/app/api/v1/launcher/service.py`** —
* `launch()`: for `mode == interview`, `run_id = factory_runs.new_run_id()`, stored
  on the row and passed to the spawner along with `interview=True`; autonomous
  passes `run_id=None, interview=False`.
* `read_interview(launch)`: derives the state — not an interview launch or no run
  id → `not_interview`; no run dir / no `run.json` → `starting`; `answers.json`
  present → `answered`; `questions.json` present and `run.json` state is
  `waiting_input` → `waiting` (questions returned); `run.json` state `finished`/`stopped`
  → `finished`; otherwise `running`.
* `list_launches(db, limit)`: the rows, each with `interview` computed for
  interview-mode rows and `null` otherwise.
* `submit_answers(db, launch_id, body, resume_spawner)`: 404 when the launch is
  unknown; 409 when its state is not `waiting` (this is also the double-submit
  guard — a second POST cannot spawn a second resume); 400 when the answer ids are
  not exactly the question ids, one each, or any answer is blank after strip;
  otherwise write `answers.json`, spawn the resume, store the new pid on the row,
  commit, and return `resumed: true`. The spawn happens after the file exists —
  a resume that starts before its answers are on disk would refuse itself.

**`backend/app/api/v1/launcher/routes.py`** — three routes, parsing and shaping only:
`GET /launcher/launches` (`listSessionLaunches`), `GET /launcher/launches/{launch_id}/interview`
(`getLaunchInterview`), `POST /launcher/launches/{launch_id}/answers`
(`submitInterviewAnswers`, injecting `ResumeSpawner`).

**`backend/app/core/exceptions.py`** — `LaunchNotFoundError` (404),
`InterviewNotWaitingError` (409), `InterviewAnswerMismatchError` (400). One-line
docstrings, the existing style; the single `DomainError` handler in `app/main.py:66`
maps them with no extra wiring.

### Frontend

**`frontend/openapi.json`** and **`frontend/src/api/generated/api.ts`** — the new
models, the three `LauncherApi` methods and the `run_id` field on
`SessionLaunchRead`, matching what FastAPI emits for these signatures.
`npm run generate:api:local` reproduces the client from the checked-in schema
where a shell is available; without one, edit both by hand exactly as the previous
round of this feature did, mirroring the existing launcher entries.

**`frontend/src/features/sessions/queries.ts`** — `sessionLaunchesQueryAtom`
(`api.launcher.listSessionLaunches`, `refetchInterval` 5000 with
`refetchIntervalInBackground: true`; slower than the 2500ms run poll because each
tick reads files, not rows) and `submitInterviewAnswersMutationAtom`, invalidating
the launches key on success.

**`frontend/src/features/sessions/components/InterviewQuestions.tsx` (new)** —
renders `null` while pending, on error, or when no launch is `waiting`. For each
waiting launch: a `Card` with the launch's request text, a `<form>` holding one
labelled required `Input` per question (label text = the question, so
`getByLabel` finds it), and an "Answer and continue" submit disabled while any
answer is blank or the mutation is pending. Success → `toast.success('Run resumed')`
with the project path as description; failure → `toast.error` with
`apiErrorMessage(err)`, matching `LaunchSessionDialog`.

**`frontend/src/features/sessions/components/SessionsListPage.tsx`** — mount
`<InterviewQuestions />` between `<TrackingBanner />` and `<Tabs>`, deliberately
outside the tabs: Radix unmounts the inactive tab, and a run waiting on the user
must not be hidden behind whichever tab they happen to be on.

**`frontend/src/features/sessions/index.ts`** — re-export the new component only
if the barrel already re-exports components of this kind; do not widen the
feature's public surface otherwise.

### Docs

**`docs/API_CONTRACT.md`** — a new "API Contract v1.26 — interview mode" section:
the schemas above, the three endpoints with their status codes, the
`questions.json`/`answers.json` file contract, the `--interview`/`--run-id` argv,
and the run-dir resolution rule. It must also correct the v1.25 line
"**`mode` is stored and shown, not yet acted on**" (`docs/API_CONTRACT.md:2322-2324`)
— leaving it would make the contract contradict itself.

---

## Data / contract impact

* **DB**: one nullable column, `session_launches.run_id` (`String(64)`), Alembic
  `0022_session_launch_run_id` on the current single head `0021_work_assignee_sprint`.
  Additive, reversible, no backfill — existing rows are autonomous launches that
  never had a run id.
* **HTTP**: three new endpoints, all additive; one new optional field on the
  existing `SessionLaunchRead`. No existing response shape loses a field, so the
  regenerated client is backward-compatible for current callers.
* **Filesystem**: a new two-file contract inside the run dir, written by two
  different processes. This is the one genuinely new coupling in the change; it is
  owned by `factory/adw/interview.py` and tested from both sides.
* **Run record**: `run.json` gains `state: "waiting_input"` and an `interview`
  boolean. Readers of the record (`--list-runs`, `--kill`, `--resume`) already
  treat any non-`running` state as terminal-for-this-process, so nothing else
  changes; older records missing `interview` read as `false`.
* **Backwards compatibility**: no `--interview`, no `--run-id` → the pipeline is
  the code path it is today, questions/answers files are never written or read,
  and the spawned argv is unchanged.

## Test strategy

**Factory (`factory/tests/test_interview.py`, existing pytest + fake-CLI harness):**
1. A `--interview` run pauses after `plan`: only the plan agent was invoked,
   `questions.json` holds one question per plan assumption in order, `run.json`
   state is `waiting_input` with the pid cleared, the plan commit is on the run
   branch, exit code is 0.
2. A plan with no assumptions does not pause — the run goes on to build and
   finishes accepted, and no `questions.json` is written.
3. `--resume` of a waiting run with no `answers.json` exits 2, names the path, and
   invokes no agent at all.
4. `--resume` with a valid `answers.json`: the saved build prompt
   (`<run_dir>/prompts/build/1.user.md`) contains every question and every answer,
   and the run completes accepted.
5. `answers.json` that answers the wrong ids / misses one / is malformed exits 2.
6. `--run-id x` puts the run in `<root>/x`; a reused id, a traversal id, and
   `--run-id` together with `--resume` are all refused with exit 2.
7. Regression: the same script without `--interview` writes no interview files and
   ends exactly as it does today.

**Backend:**
* `backend/tests/unit/test_factory_launcher.py` (extend): the autonomous argv
  assertion stays exactly as it is — that test *is* the byte-for-byte guarantee —
  plus one asserting the interview argv is that argv with `--run-id <id> --interview`
  inserted before the request text, and one for the resume argv.
* `backend/tests/unit/test_factory_runs.py` (new): the runs-root rule with and
  without a `factory.config.json` `"runs_dir"` (absolute and relative);
  `write_answers` is atomic and refuses a missing run dir; `read_questions` /
  `read_run_state` degrade to empty/`None` on missing or malformed files.
* `backend/tests/integration/test_launcher.py` (extend): the fake spawner records
  kwargs (it grows `run_id`/`interview`) and a second fake stands in for the resume
  spawner; `factory_runs_root` is monkeypatched onto `settings` pointing at
  `tmp_path`. Cases: an interview launch stores a run id and passes
  `interview=True`; an autonomous launch stores no run id and passes
  `interview=False`; the interview state endpoint reports `starting` → `waiting`
  (after a hand-written `questions.json` + `run.json`) → `answered`; POST answers
  writes `answers.json` with exactly the submitted pairs and calls the resume
  spawner once with the recorded run id; POST with a missing, extra, unknown-id or
  blank answer is 400 with the spawner never called; POST when not waiting is 409;
  POST for an unknown launch is 404; the list endpoint embeds the interview block
  for interview rows and `null` for autonomous ones.

**Frontend (`frontend/tests/components/interviewQuestions.ct.tsx`, new, Playwright CT):**
routes `**/api/v1/**` with the same CORS/preflight helper
`launchSessionDialog.ct.tsx` uses. Scenarios: two questions render two required
fields and the submit is disabled until both are filled; submitting posts
`{answers: [{id, answer}, …]}` in question order and shows "Run resumed"; a launch
that is not waiting renders nothing; a 409 surfaces the error toast and leaves the
form in place.

Everything runs under the repo's existing commands — `uv run pytest -q` /
`uv run ruff check .` in `backend/`, `pytest` in `factory/`, and the CT config in
`frontend/`.

## Risks

1. **Run-dir resolution is duplicated across the language boundary.** The backend
   mirrors `factory/adw/config.runs_root`. If the factory's rule ever changes, the
   backend silently looks in the wrong place and every interview run reads as
   `starting` forever. Mitigated by keeping the mirror in one small documented
   module with its own unit test, and by the state machine degrading to "no
   questions yet" rather than to an error.
2. **A run that dies between plan and pause leaves no questions.** The UI then
   shows nothing for that launch and the user has only the launch log. The state
   enum makes this visible (`finished` with no questions) but nothing recovers it
   automatically.
3. **The answers are user text handed to an agent.** They are written as JSON data
   and folded into a prompt, never into argv or a shell string, so the injection
   surface is the same one `request_text` already has — but a hostile answer can
   still steer the builder. Out of scope to defend beyond not making it worse.
4. **Double resume.** Two POSTs racing could spawn two resumes on one run dir. The
   409-unless-`waiting` guard plus `answers.json` existing before the spawn closes
   the common case; a true simultaneous race would still need a lock, which this
   change does not add. The factory's own `plan_resume` refuses a run whose pid is
   still alive, which catches most of the rest.
5. **`assumptions[]` is only as good as the planner.** Interview mode surfaces
   exactly what the plan role chose to declare; a plan that declares none simply
   does not pause. That is the honest behaviour, but it will read as "interview
   mode did nothing" to a user who expected to be asked something.
6. **Widening `LaunchSpawner` touches existing tests.** The Protocol is
   keyword-only, so every current call site and the existing fake must be updated
   in the same commit; a missed one fails at runtime, not at import.
7. **The launches endpoint does synchronous file IO in an async route.** It is a
   handful of small reads per interview launch, bounded by the list limit, and it
   matches what `list_projects` already does — but it is on the event loop, polled
   every 5 seconds per open tab.
8. **The client is regenerated by hand if no shell is available.** A hand-written
   `api.ts` that drifts from what the generator would emit is the failure mode the
   previous round already risked; the checked-in `frontend/openapi.json` must be
   updated in the same edit so `generate:api:local` reproduces it exactly.
9. **New run state in an old reader.** Anything that treats `run.json` states as a
   closed set of three now sees a fourth. Inside this repo only `--list-runs` and
   `--kill` read it and both handle it correctly, but an external script would not.

## Assumptions

* A plan whose envelope declares no assumptions does **not** pause: there is
  nothing to ask, so the run continues to build. The alternative (always pause,
  possibly with an empty question list) would strand a run nobody can answer.
* Questions are exactly the plan envelope's `assumptions[]`, verbatim, one
  question each — no LLM rewriting into question form. It keeps the pause
  deterministic and free.
* Only interview launches get a server-generated run id; autonomous launches keep
  `run_id = NULL`, because giving them one would change their argv and the stated
  done-criterion forbids that.
* "The session/run surface that shows launched runs" is read as the Sessions list
  page: no endpoint listing launches exists yet, so `GET /launcher/launches` is
  added as part of this change — without it the UI has no way to discover a
  waiting run.
* All answers are required (the request says so), so the submit stays disabled
  until every field is non-blank; there is no "skip this question" path.
