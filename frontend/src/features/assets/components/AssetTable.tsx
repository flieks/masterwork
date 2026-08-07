import { useNavigate } from 'react-router-dom';
import type { AssetSummary } from '~/api/generated';
import { absoluteDate } from '~/lib/datetime';
import { assetDetailPath, type AssetKind } from '../queries';
import { ProviderBadge } from './ProviderBadge';

interface AssetTableProps {
  kind: AssetKind;
  assets: AssetSummary[];
}

export function AssetTable({ kind, assets }: AssetTableProps) {
  const navigate = useNavigate();

  return (
    <div className="overflow-x-auto rounded-lg border">
      <table className="w-full text-sm">
        <thead className="border-b bg-muted/40 text-left text-xs uppercase tracking-wide text-muted-foreground">
          <tr>
            <th className="px-4 py-2.5 font-medium">Name</th>
            <th className="px-4 py-2.5 font-medium">Description</th>
            <th className="px-4 py-2.5 font-medium">Provider</th>
            <th className="px-4 py-2.5 font-medium">Updated</th>
          </tr>
        </thead>
        <tbody>
          {assets.map((asset) => (
            <tr
              key={asset.id}
              tabIndex={0}
              role="link"
              onClick={() => navigate(assetDetailPath(kind, asset.name, asset.provider))}
              onKeyDown={(e) => {
                if (e.key === 'Enter') navigate(assetDetailPath(kind, asset.name, asset.provider));
              }}
              className="cursor-pointer border-b transition-colors last:border-0 hover:bg-accent/50 focus-visible:bg-accent/50 focus-visible:outline-none"
            >
              <td className="max-w-[16rem] truncate px-4 py-2.5 font-medium" title={asset.title}>
                {asset.title}
              </td>
              <td className="max-w-[28rem] truncate px-4 py-2.5 text-muted-foreground">
                {asset.description || '—'}
              </td>
              <td className="px-4 py-2.5">
                <ProviderBadge provider={asset.provider} />
              </td>
              <td className="whitespace-nowrap px-4 py-2.5 text-muted-foreground">
                {absoluteDate(asset.updated_at)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
