import { atomFamily } from 'jotai/utils';
import { atomWithQuery, atomWithMutation } from 'jotai-tanstack-query';
import { api, GENERATE_TIMEOUT_MS, isNotFoundError } from '~/api/client';
import type { AssetDetail, AssetDiagram, AssetSessionUse, CodingAssetUsage } from '~/api/generated';
// The rollup owns the inspection scope; the drill-in follows it.
import { includeInspectionAtom } from '~/features/sessions/queries';
import type { AssetKind } from './paths';

// Identity and URLs live in a client-free leaf so the sessions screen can link
// to an asset without importing the API client.
export {
  PROVIDER,
  buildAssetId,
  parseAssetId,
  assetListPath,
  assetDetailPath,
  type AssetKind,
  type ParsedAssetId,
} from './paths';

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

/**
 * How much every installed asset of one kind has actually been used, keyed by
 * the name the runs recorded — which is what a card and a table row read.
 *
 * Keyed by name rather than by asset id: a plugin asset is recorded under the
 * name Claude Code calls it by ("vercel:deploy"), while its id names the
 * provider that installed it. The Sessions rollup shares this query's key, so
 * it and these pages hit one cache entry — hence the trailing `false`, which is
 * the rollup's `include_inspection` default. These pages have no toggle: a card
 * always reads the honest count.
 */
export const assetUsageByNameAtom = atomFamily((kind: AssetKind) =>
  atomWithQuery(() => ({
    queryKey: ['codingAssetUsage', 'all', kind, false],
    queryFn: async (): Promise<Map<string, CodingAssetUsage>> => {
      const { data } = await api.coding.listCodingAssetUsage(undefined, kind, false);
      return new Map(data.map((row) => [row.name, row]));
    },
  })),
);

/**
 * The runs that used one asset, newest first, each with the calls it made.
 * Scoped by the rollup's inspection toggle so the drill-in counts the same runs
 * the table that linked here did.
 */
export const assetSessionUsesQueryAtom = atomFamily((assetId: string) =>
  atomWithQuery((get) => {
    const includeInspection = get(includeInspectionAtom);
    return {
      queryKey: ['assetSessionUses', assetId, includeInspection],
      queryFn: async (): Promise<AssetSessionUse[]> =>
        (await api.coding.listAssetSessionUses(assetId, undefined, includeInspection)).data,
      enabled: assetId.length > 0,
    };
  }),
);

export const generateAssetDiagramMutationAtom = atomWithMutation(() => ({
  // One-shot claude -p (up to 300 s) — no client timeout, same as chat sends.
  mutationFn: (assetId: string): Promise<AssetDiagram> =>
    api.assets.generateAssetDiagram(assetId, { timeout: GENERATE_TIMEOUT_MS }).then((r) => r.data),
}));
