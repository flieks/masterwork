import { useAtom } from 'jotai';
import { Link } from 'react-router-dom';
import { Activity } from 'lucide-react';
import type { AssetSummary, CodingAssetUsage } from '~/api/generated';
import { Card, CardContent, CardHeader, CardTitle } from '~/components/ui/card';
import { absoluteDateTime, relativeTime } from '~/lib/datetime';
import { assetDetailPath, assetUsageByNameAtom, type AssetKind } from '../queries';
import { usageLabel } from '../usage';
import { AssetDatesStacked } from './AssetDates';
import { ModelBadge } from './ModelBadge';

interface AssetGridProps {
  kind: AssetKind;
  assets: AssetSummary[];
}

export function AssetGrid({ kind, assets }: AssetGridProps) {
  const [{ data: usage, isPending }] = useAtom(assetUsageByNameAtom(kind));

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {assets.map((asset) => (
        <Link
          key={asset.id}
          to={assetDetailPath(kind, asset.name, asset.provider)}
          className="rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Card className="relative h-full transition-colors hover:border-ring hover:bg-accent/40">
            <CardHeader>
              <CardTitle className="truncate" title={asset.title}>
                {asset.title}
              </CardTitle>
            </CardHeader>
            <CardContent className="flex h-full flex-col gap-3 pb-20">
              <p className="line-clamp-3 text-sm text-muted-foreground">
                {asset.description || <span className="italic">No description</span>}
              </p>
            </CardContent>
            <div className="absolute inset-x-4 bottom-4 flex flex-col gap-1.5">
              <UsageLine usage={usage?.get(asset.name)} pending={isPending} />
              <div className="flex items-end justify-between gap-2">
                <AssetDatesStacked created={asset.created_at} updated={asset.updated_at} />
                <ModelBadge model={asset.model} showInherit={kind === 'agent'} compact />
              </div>
            </div>
          </Card>
        </Link>
      ))}
    </div>
  );
}

/**
 * What runs have done with this asset. Blank while the rollup is still loading
 * rather than claiming "never used" — an asset nobody has run and one whose
 * counts have not arrived look identical, and only one of them is true.
 */
function UsageLine({ usage, pending }: { usage: CodingAssetUsage | undefined; pending: boolean }) {
  const label = usageLabel(usage);
  if (pending) return <span className="text-xs">&nbsp;</span>;
  if (!label || !usage) return <span className="text-xs text-muted-foreground/70">Never used</span>;
  return (
    <span className="flex items-center gap-1.5 text-xs">
      <Activity className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
      <span className="font-medium">{label}</span>
      <time
        className="truncate text-muted-foreground"
        dateTime={usage.last_used_at}
        title={absoluteDateTime(usage.last_used_at)}
      >
        · {relativeTime(usage.last_used_at)}
      </time>
    </span>
  );
}
