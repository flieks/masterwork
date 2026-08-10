import { HelpCircle } from 'lucide-react';
import type { CodingSessionDetail } from '~/api/generated';
import { hasEvidence, unattributedEvidence } from '../evidence';
import { EvidenceSections } from './EvidenceSections';

/**
 * Evidence that names no stage still has to be readable. The ingest never
 * rejects, so a gate can fire before the first `phase_start`, and a rebuild can
 * leave a re-pointed row honestly unlinked — both cases land here rather than
 * being filtered out of the phase panel and lost.
 */
export function UnattributedEvidence({ session }: { session: CodingSessionDetail }) {
  const evidence = unattributedEvidence(session);
  if (!hasEvidence(evidence)) return null;

  return (
    <section
      aria-label="Unattributed evidence"
      className="flex flex-col gap-3 rounded-lg border border-dashed bg-card p-4"
    >
      <header>
        <h3 className="flex items-center gap-1.5 text-sm font-semibold">
          <HelpCircle className="size-4 text-muted-foreground" aria-hidden="true" />
          Unattributed evidence
        </h3>
        <p className="mt-0.5 text-xs text-muted-foreground">
          These rows name no stage — a gate that fired before any stage started, or evidence a
          rebuild could not re-link. They are shown here so nothing is dropped.
        </p>
      </header>
      <EvidenceSections evidence={evidence} />
    </section>
  );
}
