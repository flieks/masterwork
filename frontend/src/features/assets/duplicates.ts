import type { AssetSummary } from '~/api/generated';

/**
 * Drop the unlinked generic row that an agent-folder twin already stands in
 * for. skills.sh's CLI leaves one real copy per folder, so the same skill
 * would list twice; the agent copy carries the twin badge and the Merge
 * control, the orphaned generic copy adds nothing. A generic copy some agent
 * does link to stays: that row says who sees it.
 */
export function hideMergedTwins(assets: AssetSummary[]): AssetSummary[] {
  const twinned = new Set(assets.flatMap((a) => (a.generic_twin ? [a.name] : [])));
  return assets.filter(
    (a) => !(a.provider === 'generic' && a.agents.length === 0 && twinned.has(a.name)),
  );
}
