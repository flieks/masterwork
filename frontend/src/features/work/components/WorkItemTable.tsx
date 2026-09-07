import { useState } from 'react';
import { ChevronDown, ChevronRight, ExternalLink } from 'lucide-react';
import type { WorkItem } from '~/api/generated';
import { Badge, type BadgeProps } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
import { cn } from '~/lib/utils';
import { isHttpUrl, sprintLabel, type WorkItemNode } from '../queries';

interface WorkItemTableProps {
  nodes: WorkItemNode[];
  onStart: (item: WorkItem) => void;
  onOpen: (item: WorkItem) => void;
  /** The item whose prompt is being assembled right now, if any. */
  startingId: number | null;
  /** Filters are on: groups default to open, so a matching child is never hidden. */
  forceExpanded: boolean;
}

/** Rem per nesting level. Below ~1.5 the tree stops reading as a tree. */
const INDENT = 1.75;

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

export function WorkItemTable({
  nodes,
  onStart,
  onOpen,
  startingId,
  forceExpanded,
}: WorkItemTableProps) {
  // Only the groups the reader touched; everything else follows `forceExpanded`,
  // so a chevron still works while a filter holds the groups open.
  const [overrides, setOverrides] = useState<Map<number, boolean>>(new Map());

  function toggle(id: number, isOpen: boolean) {
    setOverrides((current) => new Map(current).set(id, !isOpen));
  }

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
          {nodes.map((node) => (
            <WorkItemRows
              key={node.item.id}
              node={node}
              depth={0}
              overrides={overrides}
              forceExpanded={forceExpanded}
              onToggle={toggle}
              onStart={onStart}
              onOpen={onOpen}
              startingId={startingId}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

interface WorkItemRowsProps {
  node: WorkItemNode;
  depth: number;
  overrides: Map<number, boolean>;
  forceExpanded: boolean;
  onToggle: (id: number, isOpen: boolean) => void;
  onStart: (item: WorkItem) => void;
  onOpen: (item: WorkItem) => void;
  startingId: number | null;
}

function WorkItemRows({
  node,
  depth,
  overrides,
  forceExpanded,
  onToggle,
  onStart,
  onOpen,
  startingId,
}: WorkItemRowsProps) {
  const { item, children } = node;
  const hasChildren = children.length > 0;
  const isOpen = hasChildren && (overrides.get(item.id) ?? forceExpanded);
  // Pulled in only to parent an assigned item — someone else's row.
  const context = item.pulled_as_parent;

  return (
    <>
      <tr className="border-b transition-colors last:border-0 hover:bg-accent/50">
        <td className="whitespace-nowrap px-4 py-2.5">
          <Badge variant={typeVariant(item.item_type)} className={cn(context && 'opacity-60')}>
            {item.item_type}
          </Badge>
        </td>

        <td className="max-w-[30rem] px-4 py-2.5">
          <div
            className="flex items-center gap-1.5"
            style={{ paddingLeft: `${depth * INDENT}rem` }}
          >
            {hasChildren ? (
              <button
                type="button"
                aria-expanded={isOpen}
                aria-label={`${isOpen ? 'Collapse' : 'Expand'} ${item.title}`}
                onClick={() => onToggle(item.id, isOpen)}
                className="shrink-0 rounded text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {isOpen ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
              </button>
            ) : (
              <span className="size-4 shrink-0" />
            )}

            {/* Title comes from DevOps — plain text, never rendered as markup. */}
            <button
              type="button"
              onClick={() => onOpen(item)}
              title={item.title}
              className={cn(
                'truncate text-left font-medium hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                context && 'font-normal text-muted-foreground',
              )}
            >
              {item.title}
            </button>

            {context ? (
              <Badge variant="muted" className="shrink-0">
                context
              </Badge>
            ) : null}

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
          <span
            className="font-mono text-xs text-muted-foreground"
            style={{ paddingLeft: `${depth * INDENT + 1.375}rem` }}
          >
            #{item.external_id}
          </span>
        </td>

        <td className="whitespace-nowrap px-4 py-2.5 text-muted-foreground">{item.state}</td>
        <td
          className="max-w-[14rem] truncate px-4 py-2.5 text-muted-foreground"
          title={item.iteration ?? undefined}
        >
          {item.iteration ? sprintLabel(item.iteration) : '—'}
        </td>
        <td className="px-4 py-2.5 text-right font-mono text-xs tabular-nums text-muted-foreground">
          {item.priority ?? '—'}
        </td>
        <td className="whitespace-nowrap px-4 py-2.5 text-right">
          {context ? null : (
            <Button
              variant="outline"
              size="sm"
              disabled={startingId !== null}
              onClick={() => onStart(item)}
            >
              {startingId === item.id ? 'Starting…' : 'Start session'}
            </Button>
          )}
        </td>
      </tr>

      {isOpen
        ? children.map((child) => (
            <WorkItemRows
              key={child.item.id}
              node={child}
              depth={depth + 1}
              overrides={overrides}
              forceExpanded={forceExpanded}
              onToggle={onToggle}
              onStart={onStart}
              onOpen={onOpen}
              startingId={startingId}
            />
          ))
        : null}
    </>
  );
}
