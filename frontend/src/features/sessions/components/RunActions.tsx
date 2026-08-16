import { Link } from 'react-router-dom';
import type { FactoryRun } from '~/api/generated';
import { sessionDetailPath } from '../runs';
import { CheckDoneButton } from './CheckDoneButton';
import { DismissRunButton } from './DismissRunButton';
import { RerunRunButton } from './RerunRunButton';
import { ResumeRunButton } from './ResumeRunButton';

/**
 * What a run row offers: Resume when the factory would accept one, otherwise
 * the reason it would not — plus a way forward. A request someone already
 * re-ran points at that newer run instead of offering a second rerun, so two
 * runs of one request are never started by accident.
 */
/** Only a run that stopped short wants running again — a live one is busy,
 * and a done one got what it came for. */
function canRerun(run: FactoryRun): boolean {
  return run.outcome === 'failed' || run.outcome === 'stopped';
}

export function RunActions({ run }: { run: FactoryRun }) {
  // A dismissed run is only ever seen in the folded-away view, where the one
  // thing to offer is putting it back.
  if (run.dismissed) return <DismissRunButton run={run} dismissed={false} />;
  if (run.resumable) {
    return (
      <div className="flex shrink-0 items-center gap-1">
        <ResumeRunButton run={run} />
        <DismissRunButton run={run} dismissed />
      </div>
    );
  }

  return (
    <div className="flex shrink-0 items-center gap-2">
      <span className="text-xs text-muted-foreground">{run.resume_hint}</span>
      {run.superseded_by ? (
        <Link
          to={sessionDetailPath(`factory-${run.superseded_by}`)}
          className="shrink-0 whitespace-nowrap text-xs underline-offset-2 hover:underline"
        >
          Running again as {run.superseded_by}
        </Link>
      ) : canRerun(run) ? (
        <>
          <CheckDoneButton run={run} />
          <RerunRunButton run={run} />
        </>
      ) : null}
      <DismissRunButton run={run} dismissed />
    </div>
  );
}
