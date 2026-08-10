import type { PhaseEvidence } from '../evidence';
import { EnvelopeAttemptList } from './EnvelopeAttemptList';
import { GateCheckList } from './GateCheckList';

/** The two evidence tables, wherever they hang: a stage, or nothing at all. */
export function EvidenceSections({ evidence }: { evidence: PhaseEvidence }) {
  return (
    <>
      {evidence.gateChecks.length > 0 ? (
        <section aria-label="Gate checks" className="border-t pt-3">
          <h4 className="mb-2 text-[11px] uppercase tracking-wide text-muted-foreground">
            Gate checks
          </h4>
          <GateCheckList checks={evidence.gateChecks} />
        </section>
      ) : null}

      {evidence.envelopes.length > 0 ? (
        <section aria-label="Envelope attempts" className="border-t pt-3">
          <h4 className="mb-2 text-[11px] uppercase tracking-wide text-muted-foreground">
            Envelope attempts
          </h4>
          <EnvelopeAttemptList envelopes={evidence.envelopes} />
        </section>
      ) : null}
    </>
  );
}
