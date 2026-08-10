import type { CodingSessionDetail, EnvelopeAttempt, GateCheckItem } from '~/api/generated';

/**
 * Evidence (v1.19) hangs off the run detail as two flat sibling arrays, each row
 * carrying a nullable `phase_id`. Flat is deliberate: a gate can fire before any
 * stage started, and a rebuild can leave a re-pointed row honestly unlinked. So
 * "group by phase" here always has a bucket for the rows that name no phase —
 * dropping them would hide exactly the evidence the shape exists to preserve.
 */
export interface PhaseEvidence {
  envelopes: EnvelopeAttempt[];
  gateChecks: GateCheckItem[];
}

const EMPTY: PhaseEvidence = { envelopes: [], gateChecks: [] };

export function evidenceForPhase(session: CodingSessionDetail, phaseId: number): PhaseEvidence {
  return {
    envelopes: session.envelopes.filter((row) => row.phase_id === phaseId),
    gateChecks: session.gate_checks.filter((row) => row.phase_id === phaseId),
  };
}

/** Rows no stage claims. Rendered at run level so they are never silently lost. */
export function unattributedEvidence(session: CodingSessionDetail): PhaseEvidence {
  const envelopes = session.envelopes.filter((row) => row.phase_id === null);
  const gateChecks = session.gate_checks.filter((row) => row.phase_id === null);
  if (envelopes.length === 0 && gateChecks.length === 0) return EMPTY;
  return { envelopes, gateChecks };
}

export function hasEvidence(evidence: PhaseEvidence): boolean {
  return evidence.envelopes.length > 0 || evidence.gateChecks.length > 0;
}

/** One correction round: the gates that ran together, split by verdict. */
export interface GateAttemptGroup {
  attempt: number;
  failed: GateCheckItem[];
  passed: GateCheckItem[];
}

/**
 * Attempt is the axis that tells the story — *changed_files failed on 1 and
 * passed on 2* is the correction round, and it is unreadable interleaved.
 */
export function groupChecksByAttempt(checks: GateCheckItem[]): GateAttemptGroup[] {
  const byAttempt = new Map<number, GateAttemptGroup>();
  for (const check of checks) {
    let group = byAttempt.get(check.attempt);
    if (!group) {
      group = { attempt: check.attempt, failed: [], passed: [] };
      byAttempt.set(check.attempt, group);
    }
    (check.ok ? group.passed : group.failed).push(check);
  }
  return [...byAttempt.values()].sort((a, b) => a.attempt - b.attempt);
}

/** `changed_files` alone, or `checks · python3 -m unittest` when a row names its item. */
export function checkLabel(check: GateCheckItem): string {
  return check.item ? `${check.gate} · ${check.item}` : check.gate;
}

/** Oldest attempt first, matching the order the contract already promises. */
export function sortAttempts(envelopes: EnvelopeAttempt[]): EnvelopeAttempt[] {
  return [...envelopes].sort((a, b) => a.attempt - b.attempt || a.id - b.id);
}

/**
 * A `recovered` row was rebuilt from the event stream, which never carried a
 * body. Saying so is the point: *no body recorded* is a fact about masterwork's
 * history, not about the agent.
 */
export function isRecovered(row: { origin: string }): boolean {
  return row.origin === 'recovered';
}
