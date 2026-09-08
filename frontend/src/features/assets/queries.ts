import { atomFamily } from 'jotai/utils';
import { atomWithQuery, atomWithMutation, queryClientAtom } from 'jotai-tanstack-query';
import { api, GENERATE_TIMEOUT_MS, isNotFoundError } from '~/api/client';
import type {
  AssetDetail,
  AssetDiagram,
  AssetMigrationResult,
  AssetSessionUse,
  CatalogSearchResponse,
  CatalogSkillDetail,
  CodingAssetUsage,
  InstalledSkill,
  SkillInstallRequest,
  UpstreamCheckResult,
} from '~/api/generated';
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

/** Park a skill under .disabled/ or bring it back; same id, new path. */
export const setAssetEnabledMutationAtom = atomWithMutation<
  AssetDetail,
  { assetId: string; enabled: boolean }
>((get) => ({
  mutationFn: ({ assetId, enabled }): Promise<AssetDetail> =>
    api.assets.setAssetEnabled(assetId, { enabled }).then((r) => r.data),
  onSuccess: (asset) => {
    const queryClient = get(queryClientAtom);
    queryClient.setQueryData(['asset', asset.id], asset);
    queryClient.invalidateQueries({ queryKey: ['assets'] });
  },
}));

/** Move a Claude or Codex skill into ~/.agents/skills; it comes back under a new id. */
export const migrateAssetMutationAtom = atomWithMutation<
  AssetMigrationResult,
  { assetId: string; replaceGeneric?: boolean }
>((get) => ({
  mutationFn: ({ assetId, replaceGeneric = false }): Promise<AssetMigrationResult> =>
    api.assets
      .migrateAssetToGeneric(assetId, { replace_generic: replaceGeneric })
      .then((r) => r.data),
  onSuccess: (result) => {
    const queryClient = get(queryClientAtom);
    queryClient.setQueryData(['asset', result.asset.id], result.asset);
    queryClient.invalidateQueries({ queryKey: ['assets'] });
    queryClient.invalidateQueries({ queryKey: ['projects'] });
  },
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

// --- community skill catalog ------------------------------------------

/** One entry per trimmed query string; empty string never fires a search. */
export const skillCatalogQueryAtom = atomFamily((query: string) =>
  atomWithQuery(() => ({
    queryKey: ['skillCatalog', query],
    queryFn: async (): Promise<CatalogSearchResponse> =>
      (await api.skills.searchSkillCatalog(query)).data,
    enabled: query.length > 0,
  })),
);

interface CatalogSkillKey {
  owner: string;
  repo: string;
  skill: string;
}

export function catalogSkillKey(owner: string, repo: string, skill: string): string {
  return JSON.stringify({ owner, repo, skill } satisfies CatalogSkillKey);
}

export const catalogSkillQueryAtom = atomFamily((keyJson: string) =>
  atomWithQuery(() => {
    const { owner, repo, skill } = JSON.parse(keyJson) as CatalogSkillKey;
    return {
      // Shares a prefix with `assetsQueryAtom`'s ['assets', ...] keys so an
      // install invalidates both the installed list and this preview.
      queryKey: ['assets', 'skill', owner, repo, skill],
      queryFn: async (): Promise<CatalogSkillDetail> =>
        (await api.skills.getCatalogSkill(owner, repo, skill)).data,
      enabled: owner.length > 0 && repo.length > 0 && skill.length > 0,
    };
  }),
);

export const installSkillMutationAtom = atomWithMutation<InstalledSkill, SkillInstallRequest>(
  (get) => ({
    mutationFn: (body: SkillInstallRequest): Promise<InstalledSkill> =>
      api.skills.installSkill(body).then((r) => r.data),
    onSuccess: (_data, variables) => {
      const queryClient = get(queryClientAtom);
      queryClient.invalidateQueries({ queryKey: ['assets'] });
      queryClient.invalidateQueries({ queryKey: ['installedSkills'] });
      queryClient.invalidateQueries({
        queryKey: ['assets', 'skill', variables.owner, variables.repo, variables.skill],
      });
    },
  }),
);

export const uninstallSkillMutationAtom = atomWithMutation<void, string>((get) => ({
  mutationFn: (name: string): Promise<void> =>
    api.skills.uninstallSkill(name).then(() => undefined),
  onSuccess: () => {
    const queryClient = get(queryClientAtom);
    queryClient.invalidateQueries({ queryKey: ['assets'] });
    queryClient.invalidateQueries({ queryKey: ['installedSkills'] });
  },
}));

// --- upstream drift -----------------------------------------------------

/** Every catalog install with its cached drift status — one query, read by the
 *  detail page's Upstream card and the catalog's per-card badge alike. */
export const installedSkillsQueryAtom = atomWithQuery(() => ({
  queryKey: ['installedSkills'],
  queryFn: async (): Promise<InstalledSkill[]> => (await api.skills.listInstalledSkills()).data,
}));

/** User-initiated only: each check spends GitHub quota. */
export const checkUpstreamMutationAtom = atomWithMutation<UpstreamCheckResult, string>((get) => ({
  mutationFn: (name: string): Promise<UpstreamCheckResult> =>
    api.skills.checkSkillUpstream(name).then((r) => r.data),
  onSuccess: () => get(queryClientAtom).invalidateQueries({ queryKey: ['installedSkills'] }),
}));

export const updateFromUpstreamMutationAtom = atomWithMutation<
  InstalledSkill,
  { name: string; force: boolean }
>((get) => ({
  mutationFn: ({ name, force }): Promise<InstalledSkill> =>
    api.skills.updateSkillFromUpstream(name, { force }).then((r) => r.data),
  onSuccess: () => {
    const queryClient = get(queryClientAtom);
    queryClient.invalidateQueries({ queryKey: ['installedSkills'] });
    // Prefix match: a generic skill's id differs from the install row's.
    queryClient.invalidateQueries({ queryKey: ['asset'] });
    queryClient.invalidateQueries({ queryKey: ['assets'] });
  },
}));
