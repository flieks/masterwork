import { cn } from '~/lib/utils';
import {
  RATE_UNKNOWN,
  RATE_UNKNOWN_HINT,
  denominatorLabel,
  formatRate,
  isSmallSample,
} from '../stats';

/**
 * A rate and the denominator it was computed from, always together.
 *
 * Every percentage on this screen goes through here, because the two ways a
 * rate lies are both display bugs: 100 % over two checks looks like 100 % over
 * two hundred, and an undefined rate rendered as 0 % reads as "never fails".
 * So the count is never optional, and a null rate is never a number.
 */
export function Rate({
  rate,
  count,
  noun,
  className,
}: {
  rate: number | null;
  count: number;
  noun: string;
  className?: string;
}) {
  const unknown = rate === null;
  const thin = isSmallSample(count);

  return (
    <span className={cn('inline-flex items-baseline gap-1.5 whitespace-nowrap', className)}>
      <span
        className={cn(
          'font-mono text-sm tabular-nums',
          unknown && 'text-muted-foreground',
          // A rate over a handful of observations is noise. Muted rather than
          // hidden: the row is real, and a threshold that dropped it would
          // delete the only data a young dataset has.
          !unknown && thin && 'font-normal text-muted-foreground',
        )}
        title={
          unknown
            ? RATE_UNKNOWN_HINT
            : thin
              ? `Over ${denominatorLabel(count, noun)} — too few to read as a rate.`
              : undefined
        }
      >
        {formatRate(rate)}
      </span>
      <span className="text-[11px] text-muted-foreground">{denominatorLabel(count, noun)}</span>
    </span>
  );
}

/**
 * The same rate drawn as a bar, for the tables where a column of them is meant
 * to be scanned. An unknown rate gets a dashed empty track, never a full or an
 * empty bar — both of those are claims.
 */
export function RateBar({
  rate,
  count,
  noun,
}: {
  rate: number | null;
  count: number;
  noun: string;
}) {
  const unknown = rate === null;
  const thin = isSmallSample(count);

  return (
    <span className="flex items-center gap-2">
      <Rate rate={rate} count={count} noun={noun} className="w-28 shrink-0 justify-end" />
      <span
        aria-hidden="true"
        className={cn(
          'h-1.5 w-16 shrink-0 overflow-hidden rounded-full',
          unknown ? 'border border-dashed' : 'bg-muted',
        )}
      >
        {unknown ? null : (
          <span
            className={cn(
              'block h-full rounded-full',
              thin ? 'bg-muted-foreground/40' : 'bg-destructive/70',
            )}
            style={{ width: `${Math.round((rate ?? 0) * 100)}%` }}
          />
        )}
      </span>
    </span>
  );
}

/**
 * An average that is not a rate — corrections per stage, cost per stage — held
 * to the same rule: the count it was divided by is always beside it, and an
 * average over nothing is unknown rather than zero.
 */
export function Average({
  value,
  count,
  noun,
  format,
}: {
  value: number | null;
  count: number;
  noun: string;
  format: (value: number | null) => string;
}) {
  const unknown = value === null;
  const thin = isSmallSample(count);

  return (
    <span className="inline-flex items-baseline justify-end gap-1.5 whitespace-nowrap">
      <span
        className={cn(
          'font-mono text-sm tabular-nums',
          (unknown || thin) && 'text-muted-foreground',
        )}
        title={unknown ? RATE_UNKNOWN_HINT : undefined}
      >
        {unknown ? RATE_UNKNOWN : format(value)}
      </span>
      <span className="text-[11px] text-muted-foreground">{denominatorLabel(count, noun)}</span>
    </span>
  );
}

/** A figure that a row never reported, said as such rather than as a zero. */
export function UnknownValue({ hint }: { hint?: string }) {
  return (
    <span className="font-mono text-sm text-muted-foreground" title={hint ?? RATE_UNKNOWN_HINT}>
      {RATE_UNKNOWN}
    </span>
  );
}
