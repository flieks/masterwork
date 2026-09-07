import { Link } from 'react-router-dom';
import { CheckCircle2, CircleDashed, XCircle } from 'lucide-react';
import type { FactoryRunCheck } from '~/api/generated';
import { sessionDetailPath } from '../runs';

/** First sentence of the verdict — the row has one line, not a paragraph. */
function headline(summary: string): string {
  const stop = summary.indexOf('. ');
  return stop === -1 ? summary : summary.slice(0, stop + 1);
}

/**
 * The answer a check came back with, on the run it was asked about. Replaces
 * the button that started it: a run already checked is not checked again.
 */
export function RunCheckResult({ check }: { check: FactoryRunCheck }) {
  const running = check.outcome === 'running' || check.outcome === 'waiting';
  const failed = check.outcome === 'failed' || check.outcome === 'stopped';
  const Icon = running ? CircleDashed : failed ? XCircle : CheckCircle2;

  return (
    <Link
      to={sessionDetailPath(`factory-${check.run_id}`)}
      title={check.summary ?? undefined}
      className="flex min-w-0 shrink items-center gap-1.5 text-xs text-muted-foreground underline-offset-2 hover:underline"
    >
      <Icon className={`size-3.5 shrink-0 ${running ? 'animate-pulse' : ''}`} />
      <span className="truncate">
        {running
          ? 'Checking whether this landed anyway…'
          : failed
            ? 'The check itself did not finish'
            : (check.summary && headline(check.summary)) || 'Checked'}
      </span>
    </Link>
  );
}
