import { useAtom } from 'jotai';
import { useNavigate } from 'react-router-dom';
import type { AssetSummary } from '~/api/generated';
import { absoluteDate, absoluteDateTime, relativeTime } from '~/lib/datetime';
import { assetAge } from '../dates';
import { assetDetailPath, assetUsageByNameAtom, type AssetKind } from '../queries';
import { NeverEdited, UnknownCreated } from './AssetDates';
import { ProviderBadge } from './ProviderBadge';

interface AssetTableProps {
  kind: AssetKind;
  assets: AssetSummary[];
}

export function AssetTable({ kind, assets }: AssetTableProps) {
  const navigate = useNavigate();
  const [{ data: usage }] = useAtom(assetUsageByNameAtom(kind));

  return (
    <div className="overflow-x-auto rounded-lg border">
      <table className="w-full text-sm">
        <thead className="border-b bg-muted/40 text-left text-xs uppercase tracking-wide text-muted-foreground">
          <tr>
            <th className="px-4 py-2.5 font-medium">Name</th>
            <th className="px-4 py-2.5 font-medium">Description</th>
            <th className="px-4 py-2.5 font-medium">Provider</th>
            <th className="px-4 py-2.5 text-right font-medium">Uses</th>
            <th className="px-4 py-2.5 text-right font-medium">Runs</th>
            <th className="px-4 py-2.5 font-medium">Last used</th>
            <th className="px-4 py-2.5 font-medium">Created</th>
            <th className="px-4 py-2.5 font-medium">Updated</th>
          </tr>
        </thead>
        <tbody>
          {assets.map((asset) => {
            const used = usage?.get(asset.name);
            const age = assetAge(asset.created_at, asset.updated_at);
            return (
              <tr
                key={asset.id}
                tabIndex={0}
                role="link"
                onClick={() => navigate(assetDetailPath(kind, asset.name, asset.provider))}
                onKeyDown={(e) => {
                  if (e.key === 'Enter')
                    navigate(assetDetailPath(kind, asset.name, asset.provider));
                }}
                className="cursor-pointer border-b transition-colors last:border-0 hover:bg-accent/50 focus-visible:bg-accent/50 focus-visible:outline-none"
              >
                <td className="max-w-[16rem] truncate px-4 py-2.5 font-medium" title={asset.title}>
                  {asset.title}
                </td>
                {/* Truncated at any width, so it yields room to the date columns. */}
                <td className="max-w-[15rem] truncate px-4 py-2.5 text-muted-foreground">
                  {asset.description || '—'}
                </td>
                <td className="px-4 py-2.5">
                  <ProviderBadge provider={asset.provider} />
                </td>
                <td className="px-4 py-2.5 text-right font-mono text-xs tabular-nums">
                  {used ? used.uses : <span className="text-muted-foreground">—</span>}
                </td>
                <td className="px-4 py-2.5 text-right font-mono text-xs tabular-nums">
                  {used ? used.sessions : <span className="text-muted-foreground">—</span>}
                </td>
                <td className="whitespace-nowrap px-4 py-2.5 text-muted-foreground">
                  {used ? (
                    <time dateTime={used.last_used_at} title={absoluteDateTime(used.last_used_at)}>
                      {relativeTime(used.last_used_at)}
                    </time>
                  ) : (
                    'Never'
                  )}
                </td>
                <td className="whitespace-nowrap px-4 py-2.5 text-muted-foreground">
                  {age.state === 'unknown' ? (
                    <UnknownCreated />
                  ) : (
                    <time dateTime={age.created} title={absoluteDateTime(age.created)}>
                      {absoluteDate(age.created)}
                    </time>
                  )}
                </td>
                <td className="whitespace-nowrap px-4 py-2.5 text-muted-foreground">
                  {age.state === 'written-once' ? (
                    <NeverEdited created={age.created} />
                  ) : (
                    <time dateTime={asset.updated_at} title={absoluteDateTime(asset.updated_at)}>
                      {absoluteDate(asset.updated_at)}
                    </time>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
