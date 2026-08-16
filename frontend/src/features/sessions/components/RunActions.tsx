import { Link } from 'react-router-dom';
import type { FactoryRun } from '~/api/generated';
import { sessionDetailPath } from '../runs';
import { RerunRunButton } from './RerunRunButton';
import { ResumeRunButton } from './ResumeRunButton';

/**
 * What a run row offers: Resume when the factory would accept one, otherwise
 * the reason it would not — plus a way forward. A request someone already
 * re-ran points at that newer run instead of offering a second rerun, so two
 * runs of one request are never started by accident.
 */
export function RunActions({ run }: { run: FactoryRun }) {
  if (run.resumable) return <ResumeRunButton run={run} />;

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
      ) : run.outcome === 'done' ? null : (
        <RerunRunButton run={run} />
      )}
    </div>
  );
}
