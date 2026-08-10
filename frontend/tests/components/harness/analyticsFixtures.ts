import type { GateStat, ModelStat, RoleStat, RunStat } from '~/api/generated';

/**
 * A trimmed copy of the real aggregates over masterwork's own factory runs.
 *
 * The rows kept are the ones that make the honesty rules testable: a gate whose
 * failures all land on one role, a role that ran no gate at all (so every rate
 * it has is `null`), and the unnamed model row the API sorts last.
 */

/** `envelope`: the contract the `plan` role keeps breaking — 3 of its 6 checks. */
export const ENVELOPE_GATE: GateStat = {
  gate: 'envelope',
  checks: 17,
  failures: 3,
  failure_rate: 3 / 17,
  runs: 4,
  by_role: [
    { role: 'plan', checks: 6, failures: 3, failure_rate: 0.5, runs: 4 },
    { role: 'build', checks: 3, failures: 0, failure_rate: 0, runs: 3 },
    { role: 'document', checks: 4, failures: 0, failure_rate: 0, runs: 3 },
    { role: 'review', checks: 4, failures: 0, failure_rate: 0, runs: 3 },
  ],
  top_failure_notes: [
    {
      note: '"status" must be one of ok, blocked, failed (got \'complete\'). End your reply with exactly one fenced ```json envelope block, nothing after it.',
      role: 'plan',
      occurrences: 1,
      last_seen_at: '2026-08-10T00:52:20.616663Z',
    },
    {
      note: 'missing required field(s) for the plan role: status, artifacts, changed_files. End your reply with exactly one fenced ```json envelope block, nothing after it.',
      role: 'plan',
      occurrences: 2,
      last_seen_at: '2026-08-10T00:52:07.155508Z',
    },
  ],
};

/** A gate nothing has ever tripped — its split folds away, its checks do not. */
export const BOUNDARY_GATE: GateStat = {
  gate: 'boundary',
  checks: 17,
  failures: 0,
  failure_rate: 0,
  runs: 4,
  by_role: [{ role: 'plan', checks: 6, failures: 0, failure_rate: 0, runs: 4 }],
  top_failure_notes: [],
};

export const GATE_STATS: GateStat[] = [ENVELOPE_GATE, BOUNDARY_GATE];

function role(name: string, overrides: Partial<RoleStat> = {}): RoleStat {
  return {
    role: name,
    runs: 0,
    stages: 0,
    corrections: 0,
    avg_corrections: null,
    failed_stages: 0,
    stage_failure_rate: null,
    timed_stages: 0,
    total_duration_ms: 0,
    avg_duration_ms: null,
    costed_stages: 0,
    total_cost_usd: 0,
    avg_cost_usd: null,
    tokens_in: 0,
    tokens_out: 0,
    gate_checks: 0,
    gate_failures: 0,
    gate_failure_rate: null,
    envelope_attempts: 0,
    envelope_failures: 0,
    envelope_failure_rate: null,
    ...overrides,
  };
}

/** `review` leads on corrections; `plan` fails half its envelopes. */
export const ROLE_STATS: RoleStat[] = [
  role('review', {
    runs: 12,
    stages: 12,
    corrections: 7,
    avg_corrections: 7 / 12,
    timed_stages: 12,
    total_duration_ms: 443_303,
    avg_duration_ms: 36_941.9,
    costed_stages: 12,
    total_cost_usd: 0.618831,
    avg_cost_usd: 0.05156925,
    gate_checks: 82,
    gate_failures: 9,
    gate_failure_rate: 9 / 82,
    envelope_attempts: 4,
    envelope_failures: 0,
    envelope_failure_rate: 0,
  }),
  role('plan', {
    runs: 13,
    stages: 13,
    corrections: 2,
    avg_corrections: 2 / 13,
    timed_stages: 13,
    total_duration_ms: 639_387,
    avg_duration_ms: 49_183.6,
    costed_stages: 13,
    total_cost_usd: 0.885494,
    avg_cost_usd: 0.0681149,
    gate_checks: 50,
    gate_failures: 3,
    gate_failure_rate: 0.06,
    envelope_attempts: 6,
    envelope_failures: 3,
    envelope_failure_rate: 0.5,
  }),
  // The lane that ran no gate and no envelope: every rate it has is undefined,
  // and `0%` would read as "never fails" about checks that never happened.
  role('git', {
    runs: 6,
    stages: 18,
    corrections: 0,
    avg_corrections: 0,
    timed_stages: 18,
    avg_duration_ms: 0,
  }),
];

function run(id: string, overrides: Partial<RunStat> = {}): RunStat {
  return {
    session_id: id,
    title: `run ${id}`,
    workflow: 'factory',
    git_repo: 'masterwork',
    model: 'haiku',
    status: 'success',
    accepted: true,
    started_at: '2026-08-09T23:00:00.000Z',
    ended_at: '2026-08-09T23:02:00.000Z',
    wall_ms: 120_000,
    active_ms: 90_000,
    cost_usd: 0.21,
    tokens_total: 1_200_000,
    tokens_in: 1_150_000,
    tokens_out: 50_000,
    stages: 5,
    corrections: 1,
    gates_passed: 16,
    gates_failed: 1,
    gate_checks: 17,
    gate_failures: 1,
    envelope_attempts: 6,
    envelope_failures: 1,
    child_count: 5,
    ...overrides,
  };
}

export const RUN_STATS: RunStat[] = [
  run('factory-1'),
  run('factory-2', { accepted: false, status: 'failed', cost_usd: 0.34 }),
  // A chat session: recorded, but it never reported a cost or a token count.
  run('chat-3', {
    title: 'masterwork',
    workflow: null,
    model: null,
    cost_usd: null,
    tokens_total: null,
    tokens_in: null,
    tokens_out: null,
    stages: 0,
    corrections: 0,
    gates_passed: 0,
    gates_failed: 0,
    gate_checks: 0,
    gate_failures: 0,
    envelope_attempts: 0,
    envelope_failures: 0,
    child_count: 0,
  }),
];

function model(name: string | null, overrides: Partial<ModelStat> = {}): ModelStat {
  return {
    model: name,
    lanes: 0,
    runs: 0,
    accepted_runs: 0,
    acceptance_rate: null,
    stages: 0,
    corrections: 0,
    avg_corrections: null,
    failed_stages: 0,
    timed_stages: 0,
    total_duration_ms: 0,
    avg_duration_ms: null,
    cost_usd: 0,
    tokens_in: 0,
    tokens_out: 0,
    turns: 0,
    gate_checks: 0,
    gate_failures: 0,
    gate_failure_rate: null,
    ...overrides,
  };
}

/** One real model, and the row that is not one — kept last, as the API sends it. */
export const MODEL_STATS: ModelStat[] = [
  model('haiku', {
    lanes: 23,
    runs: 7,
    accepted_runs: 4,
    acceptance_rate: 4 / 7,
    stages: 23,
    corrections: 5,
    avg_corrections: 5 / 23,
    timed_stages: 23,
    total_duration_ms: 1_028_631,
    avg_duration_ms: 44_723,
    cost_usd: 1.42273,
    tokens_in: 8_287_371,
    tokens_out: 76_924,
    turns: 28,
    gate_checks: 106,
    gate_failures: 8,
    gate_failure_rate: 8 / 106,
  }),
  model(null, {
    lanes: 145,
    runs: 83,
    accepted_runs: 46,
    acceptance_rate: 46 / 83,
    stages: 486,
    corrections: 5,
    avg_corrections: 5 / 486,
    timed_stages: 458,
    total_duration_ms: 86_969_347,
    avg_duration_ms: 189_889,
    cost_usd: 1.281442,
    tokens_in: 6_127_872,
    tokens_out: 71_529,
    turns: 430,
    gate_checks: 121,
    gate_failures: 5,
    gate_failure_rate: 5 / 121,
  }),
];
