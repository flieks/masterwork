import { absoluteDate, absoluteDateTime, relativeTime, sameDay } from '~/lib/datetime';
import { cn } from '~/lib/utils';
import { UNKNOWN_CREATED_HINT, UNKNOWN_DATE, assetAge } from '../dates';

interface AssetDatesProps {
  /** `AssetSummary.created_at` — optional in the schema, null off macOS. */
  created?: string | null;
  updated: string;
  className?: string;
}

/** A date that carries its exact instant on hover, so a day label never has to lie. */
function Stamp({
  iso,
  relative = false,
  withTime = false,
}: {
  iso: string;
  relative?: boolean;
  withTime?: boolean;
}) {
  return (
    <time dateTime={iso} title={absoluteDateTime(iso)}>
      {relative ? relativeTime(iso) : withTime ? absoluteDateTime(iso) : absoluteDate(iso)}
    </time>
  );
}

/** A birth time the platform never recorded, stated as the absence it is. */
export function UnknownCreated() {
  return <span title={UNKNOWN_CREATED_HINT}>{UNKNOWN_DATE}</span>;
}

/**
 * Stands where the edit date would go on a file that has only ever been
 * written. Printing the creation date again there would dress one event up as
 * two, and the reader has no way to tell that apart from a real same-day edit.
 */
export function NeverEdited({ created }: { created: string }) {
  return (
    <span
      className="text-muted-foreground/80"
      title={`Never edited: written once, on ${absoluteDateTime(created)}, and untouched since.`}
    >
      Never edited
    </span>
  );
}

/**
 * Both dates on one line, for the detail header — where there is room to state
 * an unknown creation date rather than quietly drop it.
 */
export function AssetDatesInline({ created, updated, className }: AssetDatesProps) {
  const age = assetAge(created, updated);
  // Written in the morning and edited after lunch is two facts that a day label
  // renders as one date printed twice. Sitting side by side, that reads as a
  // duplicate rather than as a same-day edit, so those two get their clock time.
  const withTime = age.state === 'edited' && sameDay(age.created, age.updated);

  return (
    <span className={className}>
      {'Created '}
      {age.state === 'unknown' ? (
        <UnknownCreated />
      ) : (
        <Stamp iso={age.created} withTime={withTime} />
      )}
      {' · '}
      {age.state === 'written-once' ? (
        <NeverEdited created={age.created} />
      ) : (
        <>
          {'Updated '}
          <Stamp iso={age.updated} withTime={withTime} />
        </>
      )}
    </span>
  );
}

/**
 * The same two facts stacked, for grid cards: one short line each, so neither
 * truncates behind the model badge, and the edit stays relative because that is
 * what a card is scanned for. An unknown creation date is dropped here rather
 * than repeated as a dash down a whole grid — with no column header beside it,
 * a lone dash explains nothing.
 */
export function AssetDatesStacked({ created, updated, className }: AssetDatesProps) {
  const age = assetAge(created, updated);
  return (
    <div className={cn('flex min-w-0 flex-col text-xs text-muted-foreground', className)}>
      {age.state === 'unknown' ? null : (
        <span className="truncate">
          {'Created '}
          <Stamp iso={age.created} />
        </span>
      )}
      <span className="truncate">
        {age.state === 'written-once' ? (
          <NeverEdited created={age.created} />
        ) : (
          <>
            {'Edited '}
            <Stamp iso={age.updated} relative />
          </>
        )}
      </span>
    </div>
  );
}
