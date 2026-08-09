import type {
  AgentLane,
  AssetUse,
  CodingAssetUsage,
  CodingEvent,
  CodingPhase,
  CodingSession,
  CodingSessionDetail,
} from '~/api/generated';

/**
 * A trimmed copy of the real backfilled pipeline run `factory-3f5a20b0`: five
 * phases across five lanes, 1m 55s wall clock, one correction and one failed
 * gate in `review`. Positions asserted in the tests are computed from these
 * exact timestamps.
 */
export const RUN_START = '2026-08-08T00:00:19.434Z';
export const RUN_END = '2026-08-08T00:02:14.589Z';
/** started_at → ended_at, in ms. */
export const RUN_DURATION_MS = 115_155;

function lane(name: string, overrides: Partial<AgentLane> = {}): AgentLane {
  return {
    name,
    model: null,
    color: null,
    context_tokens: null,
    context_window: null,
    cost_usd: null,
    tokens_in: null,
    tokens_out: null,
    turns: 1,
    ...overrides,
  };
}

function phase(
  seq: number,
  name: string,
  startedAt: string,
  durationMs: number | null,
  overrides: Partial<CodingPhase> = {},
): CodingPhase {
  return {
    seq,
    name,
    agent: name,
    status: 'passed',
    started_at: startedAt,
    duration_ms: durationMs,
    id: 4 + seq,
    kind: 'agent',
    description: `${name} stage description`,
    ended_at:
      durationMs === null ? null : new Date(Date.parse(startedAt) + durationMs).toISOString(),
    cost_usd: 0.05,
    tokens_in: 180_707,
    tokens_out: 2223,
    corrections: 0,
    commit_sha: null,
    gates_passed: 4,
    gates_failed: 0,
    ...overrides,
  };
}

export const FACTORY_PHASES: CodingPhase[] = [
  phase(1, 'plan', '2026-08-08T00:00:19.469Z', 30_151, {
    commit_sha: '1f91475a40bb57728436a7a9495f8873cef2ae6a',
  }),
  phase(2, 'build', '2026-08-08T00:00:49.776Z', 24_700, {
    commit_sha: '62e2dc7f65dde7faed05cbed82d92c597dcaaebd',
  }),
  phase(3, 'checks', '2026-08-08T00:01:14.584Z', 87, {
    kind: 'code',
    description: '1 check(s) passed',
    cost_usd: 0,
    tokens_in: null,
    tokens_out: null,
    gates_passed: 1,
  }),
  phase(4, 'review', '2026-08-08T00:01:14.683Z', 31_468, {
    corrections: 1,
    gates_passed: 9,
    gates_failed: 1,
    commit_sha: '8fc376ed0559636db9857073d8c9ef69a9ee1821',
  }),
  phase(5, 'document', '2026-08-08T00:01:46.261Z', 28_214, {
    commit_sha: 'd2d7c53d50560d5326d7d4c32981a9b6cbaff0e0',
  }),
];

export const FACTORY_LANES: AgentLane[] = [
  lane('plan', {
    model: 'haiku',
    color: '#6aa9ff',
    context_tokens: 20_000,
    context_window: 200_000,
  }),
  lane('build', { model: 'haiku', color: '#3ecf8e' }),
  lane('checks', { turns: 0 }),
  lane('review', { model: 'haiku', turns: 2 }),
  lane('document', { model: 'haiku' }),
];

export function assetUse(kind: string, name: string, uses: number, lane: string | null): AssetUse {
  return { kind, name, asset_id: `claude:${kind}:${name}`, lane, uses };
}

export function factoryRun(overrides: Partial<CodingSessionDetail> = {}): CodingSessionDetail {
  return {
    id: 'factory-3f5a20b0',
    cwd: '/tmp/scratch/factory-e2e',
    git_repo: 'factory-e2e',
    model: 'haiku',
    source: 'claude-code',
    launch_mode: null,
    title:
      'Add a subtract(a, b) function to calc.py, exported alongside add, with a unit test covering positive, negative and zero cases',
    title_source: 'factory',
    parent_session_id: null,
    child_count: 4,
    workflow: 'factory',
    status: 'success',
    started_at: RUN_START,
    last_event_at: RUN_END,
    ended_at: RUN_END,
    stats: { turns: 5, cost_usd: 0.192431, corrections: 1 },
    cost_usd: 0.192431,
    tokens_total: 899_924,
    tokens_in: 891_618,
    tokens_out: 8306,
    cache_read_tokens: 1_110_000,
    event_count: 82,
    tool_call_count: 0,
    duration_seconds: 115.155,
    wall_ms: RUN_DURATION_MS,
    active_ms: 114_620,
    phases: FACTORY_PHASES,
    agents: FACTORY_LANES,
    assets: [],
    ...overrides,
  };
}

/** The list endpoint returns PhaseSummary rows, not whole phases. */
export function factoryRunSummary(overrides: Partial<CodingSession> = {}): CodingSession {
  const run = factoryRun();
  return {
    ...run,
    phases: run.phases.map(({ seq, name, agent, status, started_at, duration_ms }) => ({
      seq,
      name,
      agent,
      status,
      started_at,
      duration_ms,
    })),
    ...overrides,
  };
}

/** A plain Claude Code session: synthesized `main` lane, `turn N` phases. */
export function chatRun(overrides: Partial<CodingSessionDetail> = {}): CodingSessionDetail {
  return {
    id: 'd70244ff-e3b3-4ee0-a615-12754b772de9',
    cwd: '/Users/dev/Projects/masterwork',
    git_repo: 'masterwork',
    model: 'claude-opus-4',
    source: 'claude-code',
    launch_mode: 'interactive',
    title: 'redesign the sessions screen',
    title_source: 'prompt',
    parent_session_id: null,
    child_count: 0,
    workflow: null,
    status: 'running',
    started_at: '2026-08-08T09:00:00.000Z',
    last_event_at: '2026-08-08T09:04:00.000Z',
    ended_at: null,
    stats: null,
    cost_usd: null,
    tokens_total: null,
    tokens_in: null,
    tokens_out: null,
    cache_read_tokens: null,
    event_count: 12,
    tool_call_count: 4,
    duration_seconds: 240,
    // Four minutes on the clock, 2m 31s of it actually working.
    wall_ms: 240_000,
    active_ms: 151_000,
    phases: [
      phase(1, 'turn 1', '2026-08-08T09:00:05.000Z', 9334, {
        agent: 'main',
        id: 101,
        description: null,
        commit_sha: null,
        gates_passed: 0,
        gates_failed: 0,
        cost_usd: null,
        tokens_in: null,
        tokens_out: null,
      }),
      phase(2, 'turn 2', '2026-08-08T09:02:00.000Z', null, {
        agent: 'main',
        id: 102,
        status: 'running',
        description: null,
        commit_sha: null,
        gates_passed: 0,
        gates_failed: 0,
        cost_usd: null,
        tokens_in: null,
        tokens_out: null,
      }),
    ],
    agents: [lane('main', { model: 'claude-opus-4' }), lane('backend-developer')],
    // Mirrors the real `d70244ff…`: a skill loaded by the main lane, a named
    // agent under its own lane, and the unresolved bucket with no lane at all.
    assets: [
      assetUse('agent', 'subagent', 7, null),
      assetUse('agent', 'backend-developer', 3, 'backend-developer'),
      assetUse('skill', 'agent-factory', 2, 'main'),
      assetUse('skill', 'frontend-dev', 1, 'main'),
      assetUse('agent', 'general-purpose', 1, 'main'),
    ],
    ...overrides,
  };
}

/** The rollup, shaped like the real one: `subagent` dwarfs every real name. */
export function assetUsageRows(): CodingAssetUsage[] {
  const row = (
    kind: string,
    name: string,
    sessions: number,
    uses: number,
    lastUsedAt: string,
  ): CodingAssetUsage => ({
    kind,
    name,
    asset_id: `claude:${kind}:${name}`,
    sessions,
    uses,
    last_used_at: lastUsedAt,
  });
  return [
    row('agent', 'subagent', 11, 78, '2026-08-09T14:03:06.000Z'),
    row('skill', 'agent-factory', 3, 4, '2026-08-08T21:32:45.000Z'),
    row('skill', 'frontend-dev', 3, 3, '2026-08-09T14:02:01.000Z'),
    row('agent', 'backend-developer', 1, 3, '2026-08-09T13:58:15.000Z'),
    row('skill', 'restart-backend', 1, 1, '2026-08-08T22:25:51.000Z'),
  ];
}

/**
 * The shape of the real `3fdd098b`: a long chat session whose `main` lane holds
 * a turn that never closed, and whose `subagent` lane holds nothing but spawns
 * recorded before the spawn hook existed — instants, not spans.
 */
export const DENSE_RUN_START = '2026-08-08T10:00:00.000Z';
export const DENSE_RUN_END = '2026-08-08T20:00:00.000Z';

export function denseChatRun(overrides: Partial<CodingSessionDetail> = {}): CodingSessionDetail {
  const at = (minutes: number) =>
    new Date(Date.parse(DENSE_RUN_START) + minutes * 60_000).toISOString();
  const turn = (seq: number, agent: string, minutes: number, durationMs: number | null) =>
    phase(seq, `turn ${Math.ceil(seq / 2)}`, at(minutes), durationMs, {
      agent,
      id: 200 + seq,
      kind: 'agent',
      description: null,
      commit_sha: null,
      cost_usd: null,
      tokens_in: null,
      tokens_out: null,
      gates_passed: 0,
      gates_failed: 0,
      ...(durationMs === 0 ? { description: 'start not recorded' } : {}),
      ...(durationMs === null ? { status: 'running', ended_at: null } : {}),
    });

  return chatRun({
    id: '3fdd098b-538b-4f9f-957a-86dae4f1eaea',
    status: 'success',
    started_at: DENSE_RUN_START,
    last_event_at: DENSE_RUN_END,
    ended_at: DENSE_RUN_END,
    phases: [
      turn(1, 'main', 0, 600_000),
      // Never closed: without a clamp this one claims the other nine hours.
      turn(3, 'main', 30, null),
      turn(5, 'main', 90, 120_000),
      turn(7, 'main', 95, 120_000),
      // Spawns with no recorded span, six seconds apart — close enough to
      // collide however much width the axis gives that stretch of the run.
      turn(2, 'subagent', 30, 0),
      turn(4, 'subagent', 30.1, 0),
      turn(6, 'subagent', 30.2, 0),
    ],
    agents: [lane('main', { model: 'claude-opus-4' }), lane('subagent')],
    ...overrides,
  });
}

export function toolCall(id: number, phaseId: number, createdAt: string): CodingEvent {
  return {
    id,
    session_id: 'factory-3f5a20b0',
    event_type: 'tool_call',
    tool_name: 'Read',
    payload: { tool_input: { file_path: 'calc.py' } },
    created_at: createdAt,
    phase_id: phaseId,
    agent: 'plan',
    ok: true,
    duration_ms: null,
    ended_at: null,
  };
}
