export type AssetKind = 'skill' | 'agent';

/**
 * Asset identity and URLs. A leaf module on purpose: `queries.ts` reaches the
 * API client, and anything that only needs to name or link an asset — the
 * sessions screen, a Playwright spec running in a Node worker — must be able to
 * do so without dragging the client (and `import.meta.env`) along.
 */

/** Default provider; plugin assets use "claude-plugin" (read-only). */
export const PROVIDER = 'claude';

export function buildAssetId(kind: AssetKind, name: string, provider = PROVIDER): string {
  return `${provider}:${kind}:${name}`;
}

export interface ParsedAssetId {
  provider: string;
  kind: AssetKind;
  name: string;
}

/** Parse a `{provider}:{kind}:{name}` slug; null when it isn't skill/agent. */
export function parseAssetId(id: string): ParsedAssetId | null {
  const [provider, kind, ...rest] = id.split(':');
  if (!provider || (kind !== 'skill' && kind !== 'agent') || rest.length === 0) return null;
  return { provider, kind, name: rest.join(':') };
}

export function assetListPath(kind: AssetKind): string {
  return kind === 'skill' ? '/skills' : '/agents';
}

/** Detail URL; non-default providers travel in the `p` search param. */
export function assetDetailPath(kind: AssetKind, name: string, provider = PROVIDER): string {
  const base = `${assetListPath(kind)}/${encodeURIComponent(name)}`;
  return provider === PROVIDER ? base : `${base}?p=${encodeURIComponent(provider)}`;
}
