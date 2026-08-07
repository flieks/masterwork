import { format, formatDistanceToNow, isSameDay, isToday, isYesterday } from 'date-fns';

/** "3 hours ago" — relative, for list rows. Invalid input returns "". */
export function relativeTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return formatDistanceToNow(d, { addSuffix: true });
}

/** "14:37" — clock time for a message. */
export function clockTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return format(d, 'HH:mm');
}

/** Absolute date, e.g. "Jul 16, 2026". */
export function absoluteDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return format(d, 'MMM d, yyyy');
}

/** Absolute date + time, e.g. "Jul 16, 2026, 14:37" — for tooltips over relative time. */
export function absoluteDateTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return format(d, 'MMM d, yyyy, HH:mm');
}

/** Human day label for a chat date separator: Today / Yesterday / full date. */
export function dayLabel(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  if (isToday(d)) return 'Today';
  if (isYesterday(d)) return 'Yesterday';
  return format(d, 'EEEE, MMM d, yyyy');
}

/** True when two ISO timestamps fall on the same calendar day. */
export function sameDay(a: string, b: string): boolean {
  const da = new Date(a);
  const db = new Date(b);
  if (Number.isNaN(da.getTime()) || Number.isNaN(db.getTime())) return false;
  return isSameDay(da, db);
}
