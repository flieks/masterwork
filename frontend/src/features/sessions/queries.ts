import { atom } from 'jotai';
import { atomFamily, atomWithStorage } from 'jotai/utils';
import { atomWithMutation, atomWithQuery, queryClientAtom } from 'jotai-tanstack-query';
import { api } from '~/api/client';
import type {
  AppSettings,
  AppSettingsUpdateRequest,
  CodingEvent,
  CodingSession,
  CodingSessionDetail,
  ContextSeries,
  FactoryRun,
  FactoryRunDismissRequest,
  FactoryRunResumeRead,
  FactoryRunResumeRequest,
  InterviewAnswersRequest,
  InterviewResumeRead,
  LaunchRequest,
  LauncherProject,
  LauncherProjectCreateRequest,
  SessionLaunchListItem,
  SessionLaunchRead,
} from '~/api/generated';
import { windowSince, type AssetWindow } from './runs';

const POLL_MS = 2500;

/** Backend page size for the event cursor (contract max is 1000). */
const EVENT_PAGE_SIZE = 500;

/** Safety net on the drain loop: 20 pages ≈ 10k events in one pass. */
const MAX_EVENT_PAGES = 20;

// The run vocabulary lives in a client-free leaf (`runs.ts`) so it can be used
// without pulling the API client in; re-exported here as the feature's surface.
export {
  INTERRUPTED_NEVER_DERIVED,
  LIVE_WINDOW_MS,
  isAutomatedSession,
  isSessionLive,
  projectTint,
  runIdLabel,
  runTitleMeta,
  runWorkflow,
  sessionDetailPath,
  sessionLabel,
  windowSince,
  type AssetWindow,
  type RunTitle,
} from './runs';

const LAUNCHER_PROJECTS_QUERY_KEY = ['launcherProjects'];
const APP_SETTINGS_QUERY_KEY = ['appSettings'];

/** The launcher's project picker — immediate subdirectories of projects_root. */
export const launcherProjectsQueryAtom = atomWithQuery(() => ({
  queryKey: LAUNCHER_PROJECTS_QUERY_KEY,
  queryFn: async (): Promise<LauncherProject[]> =>
    (await api.launcher.listLauncherProjects()).data,
}));

/** projects_root and any other persisted app setting. */
export const appSettingsQueryAtom = atomWithQuery(() => ({
  queryKey: APP_SETTINGS_QUERY_KEY,
  queryFn: async (): Promise<AppSettings> => (await api.settings.getSettings()).data,
}));

export const createLauncherProjectMutationAtom = atomWithMutation((get) => ({
  mutationFn: (body: LauncherProjectCreateRequest): Promise<LauncherProject> =>
    api.launcher.createLauncherProject(body).then((r) => r.data),
  onSuccess: () =>
    get(queryClientAtom).invalidateQueries({ queryKey: LAUNCHER_PROJECTS_QUERY_KEY }),
}));

export const updateSettingsMutationAtom = atomWithMutation((get) => ({
  mutationFn: (body: AppSettingsUpdateRequest): Promise<AppSettings> =>
    api.settings.updateSettings(body).then((r) => r.data),
  onSuccess: () => {
    const queryClient = get(queryClientAtom);
    queryClient.invalidateQueries({ queryKey: APP_SETTINGS_QUERY_KEY });
    // A new root points at a different set of project folders.
    queryClient.invalidateQueries({ queryKey: LAUNCHER_PROJECTS_QUERY_KEY });
  },
}));

const FACTORY_RUNS_QUERY_KEY = ['factoryRuns'];

export const launchSessionMutationAtom = atomWithMutation((get) => ({
  mutationFn: (body: LaunchRequest): Promise<SessionLaunchRead> =>
    api.launcher.launchSession(body).then((r) => r.data),
  onSuccess: () => {
    const queryClient = get(queryClientAtom);
    // The new run writes its own run dir, which the runs list reads.
    queryClient.invalidateQueries({ queryKey: FACTORY_RUNS_QUERY_KEY });
    // A session's own banner reads a different key; without this it goes on
    // offering the rerun it just started, inviting a second one.
    queryClient.invalidateQueries({ queryKey: ['runForSession'] });
  },
}));

const SESSION_LAUNCHES_QUERY_KEY = ['sessionLaunches'];

/** Recent launches with their interview state — slower than the run poll
 * since each tick reads files (questions.json/run.json), not rows. */
export const sessionLaunchesQueryAtom = atomWithQuery(() => ({
  queryKey: SESSION_LAUNCHES_QUERY_KEY,
  queryFn: async (): Promise<SessionLaunchListItem[]> =>
    (await api.launcher.listSessionLaunches()).data,
  refetchInterval: 5000,
  refetchIntervalInBackground: true,
}));

/** Every project's runs, read from the run dirs themselves — runs launched
 * from a terminal show up too, not just the ones this UI started. */
export const factoryRunsQueryAtom = atomWithQuery(() => ({
  queryKey: FACTORY_RUNS_QUERY_KEY,
  queryFn: async (): Promise<FactoryRun[]> => (await api.launcher.listFactoryRuns()).data,
  refetchInterval: 5000,
  refetchIntervalInBackground: true,
}));

/** The run that spawned one coding session, or null when no run owns it. */
export const runForSessionQueryAtom = atomFamily((sessionId: string) =>
  atomWithQuery(() => ({
    queryKey: ['runForSession', sessionId],
    queryFn: async (): Promise<FactoryRun | null> =>
      (await api.launcher.getRunForSession(sessionId)).data,
  })),
);

/** Waves a run out of the list, or brings it back. */
export const dismissFactoryRunMutationAtom = atomWithMutation((get) => ({
  mutationFn: ({
    body,
    dismissed,
  }: {
    body: FactoryRunDismissRequest;
    dismissed: boolean;
  }): Promise<FactoryRun> =>
    (dismissed
      ? api.launcher.dismissFactoryRun(body)
      : api.launcher.restoreFactoryRun(body)
    ).then((r) => r.data),
  onSuccess: () => {
    const queryClient = get(queryClientAtom);
    queryClient.invalidateQueries({ queryKey: FACTORY_RUNS_QUERY_KEY });
    queryClient.invalidateQueries({ queryKey: ['runForSession'] });
  },
}));

export const resumeFactoryRunMutationAtom = atomWithMutation((get) => ({
  mutationFn: (body: FactoryRunResumeRequest): Promise<FactoryRunResumeRead> =>
    api.launcher.resumeFactoryRun(body).then((r) => r.data),
  onSuccess: () => {
    const queryClient = get(queryClientAtom);
    queryClient.invalidateQueries({ queryKey: FACTORY_RUNS_QUERY_KEY });
    // The detail page's own banner reads a different key.
    queryClient.invalidateQueries({ queryKey: ['runForSession'] });
  },
}));

export const submitInterviewAnswersMutationAtom = atomWithMutation((get) => ({
  mutationFn: ({
    launchId,
    body,
  }: {
    launchId: number;
    body: InterviewAnswersRequest;
  }): Promise<InterviewResumeRead> =>
    api.launcher.submitInterviewAnswers(launchId, body).then((r) => r.data),
  onSuccess: () =>
    get(queryClientAtom).invalidateQueries({ queryKey: SESSION_LAUNCHES_QUERY_KEY }),
}));

function sessionQueryKey(sessionId: string): [string, string] {
  return ['codingSession', sessionId];
}

function eventsQueryKey(sessionId: string): [string, string] {
  return ['codingSessionEvents', sessionId];
}

/** Whether the list includes automated runs. Off by default, persisted. */
export const showAutomatedAtom = atomWithStorage('masterwork:sessions-show-automated', false);

/** `workflow=` filter: null means every workflow. */
export const workflowFilterAtom = atom<string | null>(null);

/** `status=` filter: null means every status. */
export const statusFilterAtom = atom<string | null>(null);

/**
 * All ingested root runs, in the order the backend chose — live first, then
 * most recent. Nothing re-sorts this list: the server already knows which run
 * matters right now, so the top-left card is the newest one.
 *
 * `roots_only`: a pipeline's stages are headless children of the run that
 * launched them. Showing them here would bury one real run under five orphan
 * cards, so the grid holds roots and each parent reveals its own stages.
 */
export const codingSessionsQueryAtom = atomWithQuery((get) => {
  const showAutomated = get(showAutomatedAtom);
  const workflow = get(workflowFilterAtom);
  const status = get(statusFilterAtom);
  return {
    // Part of the key: each filter combination is a different result, cached apart.
    queryKey: ['codingSessions', showAutomated, workflow, status],
    queryFn: async () =>
      (
        await api.coding.listCodingSessions(
          undefined,
          undefined,
          undefined,
          showAutomated,
          workflow ?? undefined,
          status ?? undefined,
          true,
        )
      ).data,
    refetchInterval: POLL_MS,
    // The user is coding in a terminal while this screen sits on another monitor,
    // so the tab is usually unfocused — keep polling anyway.
    refetchIntervalInBackground: true,
  };
});

/**
 * Every run currently blocked on a person, whatever the grid is filtered to.
 *
 * A separate query rather than a filter over the list: a question left open is
 * the one thing that must not be hidden by the filters someone set an hour ago,
 * and it is the same reason the interview form sits outside the tabs.
 * `include_automated` is on — a headless run rarely asks, but if one does, it
 * is stuck until someone notices, which is exactly the case worth surfacing.
 */
export const waitingRunsQueryAtom = atomWithQuery(() => ({
  queryKey: ['codingSessionsWaiting'],
  queryFn: async (): Promise<CodingSession[]> =>
    (
      await api.coding.listCodingSessions(
        undefined,
        undefined,
        undefined,
        true,
        undefined,
        'waiting_input',
      )
    ).data,
  refetchInterval: POLL_MS,
  refetchIntervalInBackground: true,
}));

/**
 * The stage runs one pipeline run launched, asked for by name (v1.17's
 * `parent_session_id`). This used to page the unfiltered list and pick its own
 * children out, which silently dropped every child that fell past the page —
 * a run with four stages showed none of them.
 *
 * Nothing is re-filtered here: the backend deliberately ignores
 * `include_empty`/`include_automated` in this scope, so what comes back is
 * exactly the population the parent's `child_count` counts. Only mounted once
 * the user opens the affordance, so the grid never pays for it.
 */
export const childSessionsQueryAtom = atomFamily((parentId: string) =>
  atomWithQuery(() => ({
    queryKey: ['codingSessionChildren', parentId],
    queryFn: async (): Promise<CodingSession[]> =>
      (
        await api.coding.listCodingSessions(
          undefined,
          undefined,
          undefined,
          undefined,
          undefined,
          undefined,
          undefined,
          parentId,
        )
      ).data,
    enabled: parentId.length > 0,
  })),
);

/** All time by default: the rollup answers "which assets earn their keep". */
export const assetWindowAtom = atom<AssetWindow>('all');

/** `kind=` for the rollup: null means skills and agents together. */
export const assetKindFilterAtom = atom<string | null>(null);

/**
 * Whether the rollup counts masterwork's own analysis runs. Off by default:
 * those runs Read every linked asset's SKILL.md, so counting them ranks assets
 * by how often masterwork inspected them rather than by the work they did.
 *
 * Shared with the per-asset drill-in (`assetSessionUsesQueryAtom`) so a run the
 * table counted is a run the log can show.
 */
export const includeInspectionAtom = atom(false);

/**
 * Every asset used across every run, ranked. The window token — not the
 * computed timestamp — is the cache key: a fresh `since` on each read would
 * change the key on every render and refetch forever.
 */
export const codingAssetUsageQueryAtom = atomWithQuery((get) => {
  const window = get(assetWindowAtom);
  const kind = get(assetKindFilterAtom);
  const includeInspection = get(includeInspectionAtom);
  return {
    queryKey: ['codingAssetUsage', window, kind, includeInspection],
    queryFn: async () =>
      (
        await api.coding.listCodingAssetUsage(
          windowSince(window),
          kind ?? undefined,
          includeInspection,
        )
      ).data,
  };
});

export const codingSessionQueryAtom = atomFamily((sessionId: string) =>
  atomWithQuery(() => ({
    queryKey: sessionQueryKey(sessionId),
    queryFn: async (): Promise<CodingSessionDetail> =>
      (await api.coding.getCodingSession(sessionId)).data,
    enabled: sessionId.length > 0,
    // Stop once the session is closed; its derived fields can no longer change.
    refetchInterval: (query) => (query.state.data?.ended_at ? false : POLL_MS),
    refetchIntervalInBackground: true,
  })),
);

function contextQueryKey(sessionId: string): [string, string] {
  return ['codingSessionContext', sessionId];
}

/**
 * The context-growth series for one session. Same cache-driven poll as the
 * event stream: stop once the session closes, since a closed run's transcript
 * cannot grow another sample.
 */
export const sessionContextQueryAtom = atomFamily((sessionId: string) =>
  atomWithQuery((get) => {
    const queryClient = get(queryClientAtom);
    return {
      queryKey: contextQueryKey(sessionId),
      queryFn: async (): Promise<ContextSeries> =>
        (await api.coding.readSessionContextSeries(sessionId)).data,
      enabled: sessionId.length > 0,
      refetchInterval: () => {
        const session = queryClient.getQueryData<CodingSession>(sessionQueryKey(sessionId));
        return session?.ended_at ? false : POLL_MS;
      },
      refetchIntervalInBackground: true,
    };
  }),
);

/** Drain the cursor from `after` so a long history loads in one pass, not one page per poll. */
async function fetchEventsAfter(sessionId: string, after: number): Promise<CodingEvent[]> {
  const collected: CodingEvent[] = [];
  let cursor = after;
  for (let page = 0; page < MAX_EVENT_PAGES; page++) {
    const { data } = await api.coding.listCodingSessionEvents(sessionId, cursor, EVENT_PAGE_SIZE);
    collected.push(...data);
    if (data.length < EVENT_PAGE_SIZE) break;
    cursor = data[data.length - 1].id;
  }
  return collected;
}

/**
 * The session's event stream, accumulated in the query cache: every poll asks
 * only for ids after the last one held and appends, so history is fetched once
 * and the live tail costs one small request per tick.
 */
export const codingSessionEventsQueryAtom = atomFamily((sessionId: string) =>
  atomWithQuery((get) => {
    const queryClient = get(queryClientAtom);
    const queryKey = eventsQueryKey(sessionId);
    return {
      queryKey,
      queryFn: async (): Promise<CodingEvent[]> => {
        const held = queryClient.getQueryData<CodingEvent[]>(queryKey) ?? [];
        const after = held.length > 0 ? held[held.length - 1].id : 0;
        const fresh = await fetchEventsAfter(sessionId, after);
        // Same array identity when nothing arrived — no re-render on an idle tick.
        return fresh.length > 0 ? [...held, ...fresh] : held;
      },
      enabled: sessionId.length > 0,
      // Read the session straight from the cache rather than depending on its
      // atom: the interval is re-evaluated after each fetch, so an ended session
      // still gets one final poll (picking up the tail) before polling stops.
      refetchInterval: () => {
        const session = queryClient.getQueryData<CodingSession>(sessionQueryKey(sessionId));
        return session?.ended_at ? false : POLL_MS;
      },
      refetchIntervalInBackground: true,
    };
  }),
);

/**
 * Tool calls are the texture under a phase block. Claude Code reports them as
 * `PostToolUse`; the factory runner reports its own `tool_call` events and pairs
 * each with a synthetic result row, which is not a call of its own.
 */
export function isToolCallEvent(event: CodingEvent): boolean {
  if (event.tool_name === 'tool_result') return false;
  return event.event_type === 'PostToolUse' || event.event_type === 'tool_call';
}
