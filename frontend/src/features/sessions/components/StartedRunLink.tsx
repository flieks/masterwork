import { useAtom } from 'jotai';
import { Link } from 'react-router-dom';
import { Button } from '~/components/ui/button';
import { factoryRunsQueryAtom } from '../queries';
import { sessionDetailPath } from '../runs';

/**
 * What a button becomes once it has started a run: a way into that run, where
 * the stages, events and the agent's own output already stream in. It stays a
 * plain label until the run reports itself, so the link never opens a page
 * that has nothing on it yet.
 */
export function StartedRunLink({ runId, label }: { runId: string; label: string }) {
  const [{ data: runs }] = useAtom(factoryRunsQueryAtom);
  const known = runs?.some((run) => run.run_id === runId) ?? false;

  if (!known) {
    return (
      <Button size="sm" variant="ghost" className="shrink-0 text-xs" disabled>
        {label}
      </Button>
    );
  }

  return (
    <Button asChild size="sm" variant="outline" className="shrink-0 text-xs">
      <Link to={sessionDetailPath(`factory-${runId}`)}>{label} →</Link>
    </Button>
  );
}
