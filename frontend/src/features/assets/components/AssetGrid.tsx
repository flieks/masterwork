import { Link } from 'react-router-dom';
import type { AssetSummary } from '~/api/generated';
import { Card, CardContent, CardHeader, CardTitle } from '~/components/ui/card';
import { relativeTime } from '~/lib/datetime';
import { assetDetailPath, type AssetKind } from '../queries';
import { ModelBadge } from './ModelBadge';

interface AssetGridProps {
  kind: AssetKind;
  assets: AssetSummary[];
}

export function AssetGrid({ kind, assets }: AssetGridProps) {
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
            <CardContent className="flex h-full flex-col gap-3 pb-12">
              <p className="line-clamp-3 text-sm text-muted-foreground">
                {asset.description || <span className="italic">No description</span>}
              </p>
            </CardContent>
            <div className="absolute inset-x-4 bottom-4 flex items-center justify-between gap-2">
              <span className="truncate text-xs text-muted-foreground">
                {relativeTime(asset.updated_at)}
              </span>
              <ModelBadge model={asset.model} showInherit={kind === 'agent'} compact />
            </div>
          </Card>
        </Link>
      ))}
    </div>
  );
}
