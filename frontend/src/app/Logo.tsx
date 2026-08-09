import { cn } from '~/lib/utils';

/**
 * Guild hallmark: a masterwork is the piece a journeyman submits to be made a master,
 * and it carries the maker's punch. The glyph is 180°-rotationally symmetric, so the
 * same punch reads M one way and W the other.
 */
export function Logo({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={cn('size-7', className)} aria-hidden="true">
      <rect width="32" height="32" rx="9" className="fill-primary" />
      <path
        d="M9 22V10l7 6 7-6v12"
        fill="none"
        strokeWidth="2.6"
        strokeLinecap="butt"
        strokeLinejoin="miter"
        className="stroke-primary-foreground"
      />
    </svg>
  );
}
