import type { AssetCall, CodingAssetUsage } from '~/api/generated';

/**
 * How recorded usage reads. A client-free leaf like `paths.ts` — the cards, the
 * table and the log all phrase the same numbers, and a Playwright worker must
 * be able to check the phrasing without loading the API client.
 */

/** "12 uses · 5 runs", or null when nothing recorded a use of this asset. */
export function usageLabel(usage: CodingAssetUsage | undefined): string | null {
  if (!usage || usage.uses === 0) return null;
  const uses = `${usage.uses} ${usage.uses === 1 ? 'use' : 'uses'}`;
  return `${uses} · ${usage.sessions} ${usage.sessions === 1 ? 'run' : 'runs'}`;
}

/**
 * What each signal means, in the words of the person reading the log. Only two
 * of the four carry arguments at all, which is why the source is shown next to
 * them rather than left implicit.
 */
export const CALL_SOURCE_LABELS: Record<string, string> = {
  skill_call: 'Skill call',
  spawn_call: 'Spawned',
  skill_read: 'SKILL.md read',
  subagent_stop: 'Subagent finished',
};

export function callSourceLabel(source: string): string {
  return CALL_SOURCE_LABELS[source] ?? source;
}

/** Why a call has no arguments to show — the honest empty state per signal. */
export function noInputReason(source: string): string {
  if (source === 'skill_read')
    return 'Loaded by reading its SKILL.md, so no arguments were passed.';
  if (source === 'subagent_stop')
    return 'Seen only as the subagent finishing; the spawn call was not recorded.';
  return 'No arguments were recorded for this call.';
}

/** Argument keys first in the order that reads best: what, then the brief. */
const KEY_ORDER = ['args', 'description', 'prompt', 'subagent_type', 'model', 'path'];

export function orderedInput(call: AssetCall): [string, string][] {
  const entries = Object.entries(call.input ?? {});
  return entries.sort((a, b) => {
    const ai = KEY_ORDER.indexOf(a[0]);
    const bi = KEY_ORDER.indexOf(b[0]);
    return (ai === -1 ? KEY_ORDER.length : ai) - (bi === -1 ? KEY_ORDER.length : bi);
  });
}
