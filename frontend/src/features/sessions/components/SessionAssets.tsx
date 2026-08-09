import { Sparkles } from 'lucide-react';
import type { CodingSession } from '~/api/generated';
import { groupAssetsByLane } from '../assets';
import { AssetChip } from './AssetChip';

/**
 * Which skills and agents this run reached for, grouped by the lane that used
 * them — so "the backend-developer agent loaded backend-dev" is readable off
 * the page rather than inferred. Every chip links to the asset, because seeing
 * what a run used is only useful if you can go and improve it.
 */
export function SessionAssets({ session }: { session: CodingSession }) {
  const groups = groupAssetsByLane(session.assets);

  return (
    <section className="space-y-2" aria-labelledby="assets-used">
      <h2 id="assets-used" className="flex items-center gap-1.5 text-sm font-semibold">
        <Sparkles className="size-4 text-muted-foreground" aria-hidden="true" />
        Assets used
      </h2>

      {groups.length === 0 ? (
        <p className="rounded-md border border-dashed p-3 text-xs text-muted-foreground">
          No skill or agent was recorded for this run. Skills are counted when one loads, agents
          when a subagent is dispatched — a run that used neither shows nothing here.
        </p>
      ) : (
        <div className="divide-y rounded-lg border">
          {groups.map((group) => (
            <div
              key={group.lane ?? ''}
              className="flex flex-col gap-1.5 p-2.5 sm:flex-row sm:items-start sm:gap-3"
            >
              <span
                className="shrink-0 pt-0.5 font-mono text-[11px] text-muted-foreground sm:w-32 sm:truncate"
                title={group.lane ?? 'Recorded outside any lane'}
              >
                {group.lane ?? 'no lane'}
              </span>
              <div className="flex min-w-0 flex-wrap gap-1.5">
                {group.assets.map((asset) => (
                  <AssetChip key={`${asset.kind}:${asset.name}`} asset={asset} />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
