import { cn } from '~/lib/utils';

/** Pulsing dot for a session that is open and still firing hooks. */
export function LiveIndicator({
  className,
  label = 'Live',
}: {
  className?: string;
  label?: string;
}) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 text-xs font-medium text-emerald-600 dark:text-emerald-400',
        className,
      )}
    >
      <span className="relative flex size-2">
        <span className="absolute inline-flex size-full animate-ping rounded-full bg-emerald-500 opacity-75" />
        <span className="relative inline-flex size-2 rounded-full bg-emerald-500" />
      </span>
      {label}
    </span>
  );
}
