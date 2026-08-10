/**
 * What an asset's two timestamps are allowed to say.
 *
 * `created_at` is the file's birth time and `updated_at` its last write, and
 * the pair has three readings rather than two: the platform may not record a
 * birth time at all, and a file written once reports both — which is one fact,
 * not two dates. Deciding that here keeps every surface saying the same thing.
 */

/**
 * How far apart the two stamps can be and still describe a single write.
 *
 * A file that was created and never touched does not report identical stamps:
 * the birth time and the mtime land microseconds to a few hundred milliseconds
 * apart, so testing for equality would never fire and the collapsed case would
 * be dead code. Across the 146 assets on this machine the written-once cluster
 * all sits under half a second and the next-closest pair is over an hour apart,
 * so a second is comfortably inside an empty band.
 */
const WRITTEN_ONCE_TOLERANCE_MS = 1_000;

/** What a date nobody recorded renders as. Never a zero, never the other date. */
export const UNKNOWN_DATE = '—';

export const UNKNOWN_CREATED_HINT =
  'Creation date unknown: this platform does not record a birth time for files. Absent rather than wrong — the edit date is not a stand-in for it.';

export type AssetAge =
  /** No birth time to report. Say so; never borrow `updated_at` to fill the gap. */
  | { state: 'unknown'; created: null; updated: string }
  /** Written once and untouched since — one event, so one date. */
  | { state: 'written-once'; created: string; updated: string }
  /** Two real dates, far enough apart to be two events. */
  | { state: 'edited'; created: string; updated: string };

/** Reads a pair of asset timestamps into the one thing they honestly say. */
export function assetAge(created: string | null | undefined, updated: string): AssetAge {
  if (created == null) return { state: 'unknown', created: null, updated };

  const born = new Date(created).getTime();
  const written = new Date(updated).getTime();
  // An unparseable stamp is a date we do not have, which is the unknown case.
  if (Number.isNaN(born) || Number.isNaN(written)) {
    return { state: 'unknown', created: null, updated };
  }

  // Absolute: an updated_at *before* created_at contradicts the contract, and
  // calling that "written once" would hide the contradiction behind a collapse.
  const state = Math.abs(written - born) <= WRITTEN_ONCE_TOLERANCE_MS ? 'written-once' : 'edited';
  return { state, created, updated };
}
