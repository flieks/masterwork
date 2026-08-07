// Import relatively (not via the `~` alias) so this pure helper stays importable
// from Playwright's Node test worker, which doesn't resolve the Vite alias.
import { dayLabel, sameDay } from '../../lib/datetime';

/**
 * For a chronological list of ISO timestamps, return the day-separator label to
 * show before each item (or `null` when it shares the previous item's day).
 */
export function dayLabelsFor(createdAts: string[]): (string | null)[] {
  return createdAts.map((iso, i) => {
    const prev = createdAts[i - 1];
    if (!prev || !sameDay(prev, iso)) return dayLabel(iso);
    return null;
  });
}
