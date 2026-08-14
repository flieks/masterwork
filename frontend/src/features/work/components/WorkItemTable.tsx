import { ExternalLink } from 'lucide-react';
import type { WorkItem } from '~/api/generated';
import { Badge, type BadgeProps } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
import { isHttpUrl } from '../queries';

interface WorkItemTableProps {
  items: WorkItem[];
  onStart: (item: WorkItem) => void;
  /** The item whose prompt is being assembled right now, if any. */
  startingId: number | null;
}

/** DevOps ships open type vocabularies, so unknown types fall back to neutral. */
function typeVariant(itemType: string): BadgeProps['variant'] {
  switch (itemType.toLowerCase()) {
    case 'bug':
      return 'destructive';
    case 'user story':
    case 'product backlog item':
      return 'secondary';
    default:
      return 'muted';
  }
}

export function WorkItemTable({ items, onStart, startingId }: WorkItemTableProps) {
  return (
    <div className="overflow-x-auto rounded-lg border">
      <table className="w-full text-sm">
        <thead className="border-b bg-muted/40 text-left text-xs uppercase tracking-wide text-muted-foreground">
          <tr>
            <th className="px-4 py-2.5 font-medium">Type</th>
            <th className="px-4 py-2.5 font-medium">Title</th>
            <th className="px-4 py-2.5 font-medium">State</th>
            <th className="px-4 py-2.5 font-medium">Iteration</th>
            <th className="px-4 py-2.5 text-right font-medium">Priority</th>
            <th className="px-4 py-2.5" />
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr
              key={item.id}
              className="border-b transition-colors last:border-0 hover:bg-accent/50"
            >
              <td className="whitespace-nowrap px-4 py-2.5">
                <Badge variant={typeVariant(item.item_type)}>{item.item_type}</Badge>
              </td>
              {/* Title comes from DevOps — plain text, never rendered as markup. */}
              <td className="max-w-[28rem] px-4 py-2.5">
                <div className="flex items-center gap-2">
                  <span className="truncate font-medium" title={item.title}>
                    {item.title}
                  </span>
                  {isHttpUrl(item.external_url) ? (
                    <a
                      href={item.external_url}
                      target="_blank"
                      rel="noreferrer noopener"
                      className="shrink-0 text-muted-foreground hover:text-foreground"
                      aria-label={`Open #${item.external_id} in Azure DevOps`}
                    >
                      <ExternalLink className="size-3.5" />
                    </a>
                  ) : null}
                </div>
                <span className="font-mono text-xs text-muted-foreground">#{item.external_id}</span>
              </td>
              <td className="whitespace-nowrap px-4 py-2.5 text-muted-foreground">{item.state}</td>
              <td
                className="max-w-[14rem] truncate px-4 py-2.5 text-muted-foreground"
                title={item.iteration ?? undefined}
              >
                {item.iteration ?? '—'}
              </td>
              <td className="px-4 py-2.5 text-right font-mono text-xs tabular-nums text-muted-foreground">
                {item.priority ?? '—'}
              </td>
              <td className="whitespace-nowrap px-4 py-2.5 text-right">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={startingId !== null}
                  onClick={() => onStart(item)}
                >
                  {startingId === item.id ? 'Starting…' : 'Start session'}
                </Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
