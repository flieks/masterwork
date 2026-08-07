import { atomFamily } from 'jotai/utils';
import { atomWithQuery, atomWithMutation } from 'jotai-tanstack-query';
import { api, GENERATE_TIMEOUT_MS, isNotFoundError } from '~/api/client';
import type { AssetDetail, AssetDiagram } from '~/api/generated';

export type AssetKind = 'skill' | 'agent';

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

interface AssetsListKey {
  kind: AssetKind;
  q: string;
}

/** Serialize the (kind, q) tuple so the atomFamily dedupes by value. */
export function assetsListKey(kind: AssetKind, q: string): string {
  return JSON.stringify({ kind, q } satisfies AssetsListKey);
}

export const assetsQueryAtom = atomFamily((keyJson: string) =>
  atomWithQuery(() => {
    const { kind, q } = JSON.parse(keyJson) as AssetsListKey;
    return {
      queryKey: ['assets', kind, q],
      queryFn: async () => (await api.assets.listAssets(kind, q ? q : undefined)).data,
    };
  }),
);

/** Every installed asset (no filter) — used to resolve project asset_ids. */
export const allAssetsQueryAtom = atomWithQuery(() => ({
  queryKey: ['assets'],
  queryFn: async () => (await api.assets.listAssets()).data,
}));

export const assetDetailQueryAtom = atomFamily((assetId: string) =>
  atomWithQuery(() => ({
    queryKey: ['asset', assetId],
    queryFn: async () => (await api.assets.getAsset(assetId)).data,
    enabled: assetId.length > 0,
  })),
);

export const updateAssetMutationAtom = atomWithMutation(() => ({
  mutationFn: (vars: { assetId: string; content: string }): Promise<AssetDetail> =>
    api.assets.updateAsset(vars.assetId, { content: vars.content }).then((r) => r.data),
}));

/** The cached diagram for an asset. Resolves to `null` when none exists (404). */
export const assetDiagramQueryAtom = atomFamily((assetId: string) =>
  atomWithQuery(() => ({
    queryKey: ['assetDiagram', assetId],
    queryFn: async (): Promise<AssetDiagram | null> => {
      try {
        return (await api.assets.getAssetDiagram(assetId)).data;
      } catch (err) {
        if (isNotFoundError(err)) return null;
        throw err;
      }
    },
    enabled: assetId.length > 0,
  })),
);

export const generateAssetDiagramMutationAtom = atomWithMutation(() => ({
  // One-shot claude -p (up to 300 s) — no client timeout, same as chat sends.
  mutationFn: (assetId: string): Promise<AssetDiagram> =>
    api.assets.generateAssetDiagram(assetId, { timeout: GENERATE_TIMEOUT_MS }).then((r) => r.data),
}));
