import { AssetUsageLog } from '~/features/assets/components/AssetUsageLog';
import { AssetUsagePanel } from '~/features/sessions/components/AssetUsagePanel';

/**
 * The rollup table and one asset's drill-in, in one jotai store — which is what
 * they share in the app, a route apart. Lets a test prove the inspection toggle
 * reaches both requests instead of only the table it sits on.
 */
export function AssetScopeHarness({ assetId }: { assetId: string }) {
  return (
    <div>
      <AssetUsagePanel />
      <AssetUsageLog assetId={assetId} />
    </div>
  );
}
