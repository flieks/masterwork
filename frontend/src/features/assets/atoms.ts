import { atomWithStorage } from 'jotai/utils';

export type AssetView = 'grid' | 'table';

/** Grid vs table preference, shared across /skills and /agents, persisted. */
export const assetViewAtom = atomWithStorage<AssetView>('masterwork:asset-view', 'grid');
