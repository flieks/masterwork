import type { AssetSummary } from '~/api/generated';

/**
 * Drop a generic row that its agent-folder twins already stand in for.
 * skills.sh's CLI leaves one real copy per folder, so the same skill would list
 * twice; the twin row carries the badge and the Merge control. The generic row
 * is hidden only when every agent that loads it (`agents`, v1.50: Codex
 * natively, Claude Code through a link) already has its own twin row of that
 * name — then it adds nothing. A generic copy an agent loads *instead of* a twin
 * (the usual Claude-twin case: Codex reads ~/.agents/skills) stays, because
 * that row says who sees it.
 */
export function hideMergedTwins(assets: AssetSummary[]): AssetSummary[] {
  const twinAgents = new Map<string, Set<string>>();
  for (const asset of assets) {
    if (!asset.generic_twin) continue;
    const agents = twinAgents.get(asset.name) ?? new Set<string>();
    agents.add(asset.provider);
    twinAgents.set(asset.name, agents);
  }
  return assets.filter((asset) => {
    const twins = asset.provider === 'generic' ? twinAgents.get(asset.name) : undefined;
    return !(twins && asset.agents.every((agent) => twins.has(agent)));
  });
}
