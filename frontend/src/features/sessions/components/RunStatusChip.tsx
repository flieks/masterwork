import type { LucideIcon } from 'lucide-react';
import { Badge } from '~/components/ui/badge';
import { cn } from '~/lib/utils';
import { runStatusMeta } from '../status';

/** The run's outcome, in the app's chip idiom. */
export function RunStatusChip({ status, className }: { status: string; className?: string }) {
  const meta = runStatusMeta(status);
  const Icon = meta.icon;
  return (
    <Badge
      title={meta.hint}
      className={cn('gap-1 border-transparent capitalize', meta.chip, className)}
    >
      <Icon className={cn('size-3', meta.spin && 'animate-spin')} />
      {meta.label}
    </Badge>
  );
}

/**
 * A labelled telemetry value — cost, tokens. Monospace on purpose.
 *
 * The icon is optional because some values carry their own glyph: a formatted
 * cost already starts with `$`, and pairing it with a dollar icon rendered
 * "$ $0.2716".
 */
export function StatChip({
  icon: Icon,
  label,
  value,
  className,
}: {
  icon?: LucideIcon;
  label: string;
  value: string;
  className?: string;
}) {
  return (
    <span
      title={label}
      className={cn(
        // `relative`: the sr-only label is absolutely positioned and would
        // otherwise anchor to an ancestor and widen the page.
        'relative inline-flex items-center gap-1 rounded-md bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground',
        className,
      )}
    >
      {Icon ? <Icon className="size-3 shrink-0" aria-hidden="true" /> : null}
      <span className="sr-only">{label}: </span>
      {value}
    </span>
  );
}
