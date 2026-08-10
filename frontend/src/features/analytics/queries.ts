import { atom } from 'jotai';
import { atomWithQuery } from 'jotai-tanstack-query';
import { api } from '~/api/client';
import { analyticsSince, type AnalyticsWindow } from './stats';

/**
 * The four cross-run aggregates, and the four filters they share.
 *
 * The API takes the same four parameters on every endpoint deliberately, so the
 * numbers describe one population. They are therefore one set of atoms read by
 * all four queries — there is no way to filter the gate table without the role
 * table moving with it, because a screen where they disagreed would be lying.
 */

/**
 * How many runs the trend plots — the contract's own default, and its maximum
 * is 500. Kept at 100 because each bar needs a few pixels to stay clickable:
 * asking for 500 would make the strip wider than the panel on any laptop and
 * push the whole page sideways.
 */
const RUN_TREND_LIMIT = 100;

/** How far back every aggregate looks. */
export const analyticsWindowAtom = atom<AnalyticsWindow>('all');

/** `workflow=`: null means every workflow. "chat" also matches runs that named none. */
export const analyticsWorkflowAtom = atom<string | null>(null);

/**
 * Whether masterwork's own analysis runs are counted. Off by default and for
 * the same reason the asset rollup leaves them out: those runs Read every
 * linked asset's SKILL.md, so counting them measures masterwork rather than the
 * work. Getting it wrong does not make one number wrong, it makes all of them
 * wrong together.
 */
export const analyticsIncludeInspectionAtom = atom(false);

/**
 * Whether runs launched by another run are counted. Off by default: a
 * pipeline's headless stage child is the inside view of a stage already counted
 * on its parent, so counting both puts the same work in twice and adds a `main`
 * role that did the pipeline's work a second time.
 */
export const analyticsIncludeChildrenAtom = atom(false);

interface SharedFilters {
  since: string | undefined;
  workflow: string | undefined;
  includeInspection: boolean;
  includeChildren: boolean;
  /** The window token, not the computed timestamp — see below. */
  key: readonly [AnalyticsWindow, string | null, boolean, boolean];
}

/**
 * The filters as the queries pass them. The cache key carries the window
 * *token* rather than the `since` it resolves to: a fresh timestamp on every
 * read would change the key on every render and refetch for ever.
 */
const sharedFiltersAtom = atom<SharedFilters>((get) => {
  const window = get(analyticsWindowAtom);
  const workflow = get(analyticsWorkflowAtom);
  const includeInspection = get(analyticsIncludeInspectionAtom);
  const includeChildren = get(analyticsIncludeChildrenAtom);
  return {
    since: analyticsSince(window),
    workflow: workflow ?? undefined,
    includeInspection,
    includeChildren,
    key: [window, workflow, includeInspection, includeChildren] as const,
  };
});

/**
 * Every gate, ranked by how often it failed. The only place a gate's *name* and
 * its failure *note* exist — these come from the v1.19 evidence rows, so a run
 * recorded before them contributes nothing until it is backfilled.
 */
export const gateStatsQueryAtom = atomWithQuery((get) => {
  const { since, workflow, includeInspection, includeChildren, key } = get(sharedFiltersAtom);
  return {
    queryKey: ['gateStats', ...key],
    queryFn: async () =>
      (await api.coding.listGateStats(since, workflow, includeInspection, includeChildren)).data,
  };
});

/**
 * Every lane across every run, worst first — the server orders by corrections
 * desc, then gate failures, which is already the order this screen wants. It is
 * never re-sorted here: the ranking is the answer.
 */
export const roleStatsQueryAtom = atomWithQuery((get) => {
  const { since, workflow, includeInspection, includeChildren, key } = get(sharedFiltersAtom);
  return {
    queryKey: ['roleStats', ...key],
    queryFn: async () =>
      (await api.coding.listRoleStats(since, workflow, includeInspection, includeChildren)).data,
  };
});

/** One point per run, oldest first — read left to right, that is the trend. */
export const runStatsQueryAtom = atomWithQuery((get) => {
  const { since, workflow, includeInspection, includeChildren, key } = get(sharedFiltersAtom);
  return {
    queryKey: ['runStats', ...key, RUN_TREND_LIMIT],
    queryFn: async () =>
      (
        await api.coding.listRunStats(
          since,
          workflow,
          includeInspection,
          includeChildren,
          RUN_TREND_LIMIT,
        )
      ).data,
  };
});

/** Every model, busiest first, with the unnamed row kept and sorted last. */
export const modelStatsQueryAtom = atomWithQuery((get) => {
  const { since, workflow, includeInspection, includeChildren, key } = get(sharedFiltersAtom);
  return {
    queryKey: ['modelStats', ...key],
    queryFn: async () =>
      (await api.coding.listModelStats(since, workflow, includeInspection, includeChildren)).data,
  };
});
