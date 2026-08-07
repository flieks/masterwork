import { useAtom } from 'jotai';
import { Link } from 'react-router-dom';
import { TriangleAlert } from 'lucide-react';
import type { CrossChange } from '~/api/generated';
import { relativeTime } from '~/lib/datetime';
import { crossChangesQueryAtom } from '../queries';

/** Distinct asset ids modified by OTHER projects since this project's last run. */
export function useCrossChangedAssetIds(projectId: string): Set<string> {
  const [{ data }] = useAtom(crossChangesQueryAtom(projectId));
  return new Set((data?.changes ?? []).map((c) => c.asset_id));
}

function shortName(assetId: string): string {
  return assetId.split(':').at(-1) ?? assetId;
}

/** Banner: shared assets were edited by other projects — the score may be stale. */
export function CrossChangeAlert({ projectId }: { projectId: string }) {
  const [{ data }] = useAtom(crossChangesQueryAtom(projectId));
  const changes = data?.changes ?? [];
  if (changes.length === 0) return null;

  // One line per distinct asset, newest change first (API returns newest first).
  const byAsset = new Map<string, CrossChange>();
  for (const change of changes) {
    if (!byAsset.has(change.asset_id)) byAsset.set(change.asset_id, change);
  }

  return (
    <div className="space-y-1 rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-xs">
      <p className="font-medium">
        <TriangleAlert className="mr-1.5 inline size-3.5 text-amber-500" />
        {byAsset.size === 1 ? 'A linked asset was' : `${byAsset.size} linked assets were`} changed
        by other projects since the last run — the score may be stale.
      </p>
      <ul className="space-y-0.5 pl-5 text-muted-foreground">
        {[...byAsset.values()].map((change) => (
          <li key={change.asset_id}>
            <span className="font-mono text-foreground">{shortName(change.asset_id)}</span>{' '}
            {change.action}d by{' '}
            {change.project_id ? (
              <Link to={`/projects/${change.project_id}`} className="underline">
                {change.project_name}
              </Link>
            ) : (
              'a global chat'
            )}{' '}
            ({relativeTime(change.applied_at)}) — “{change.title}”
          </li>
        ))}
      </ul>
      <p className="text-muted-foreground">Re-run the simulation to verify the score held.</p>
    </div>
  );
}
