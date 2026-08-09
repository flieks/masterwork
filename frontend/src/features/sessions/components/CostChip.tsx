import { DollarSign } from 'lucide-react';
import { formatCost } from '~/lib/timeline';
import { StatChip } from './RunStatusChip';

/**
 * What the run cost. The currency symbol appears exactly once: `formatCost`
 * writes it, so the chip drops the dollar icon that used to sit beside it and
 * render "$ $0.2716". An unknown cost formats as a bare "—" with no symbol at
 * all, so the icon comes back to keep the chip identifiable.
 */
export function CostChip({
  cost,
  className,
}: {
  cost: number | null | undefined;
  className?: string;
}) {
  const value = formatCost(cost);
  return (
    <StatChip
      icon={value === '—' ? DollarSign : undefined}
      label="Cost"
      value={value}
      className={className}
    />
  );
}
