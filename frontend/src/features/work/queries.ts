import { atomWithStorage } from 'jotai/utils';
import { atomWithMutation, atomWithQuery, queryClientAtom } from 'jotai-tanstack-query';
import { api, WORK_SYNC_TIMEOUT_MS } from '~/api/client';
import type {
  WorkItem,
  WorkItemStartResponse,
  WorkSource,
  WorkSourceCreateRequest,
  WorkSyncResult,
} from '~/api/generated';

// The item vocabulary lives in a client-free leaf (`tree.ts`) so it can be used
// without pulling the API client in; re-exported here as the feature's surface.
export {
  ASSIGNEE_ME,
  assigneeOptions,
  buildWorkItemTree,
  countNodes,
  filterWorkItemTree,
  hasActiveFilters,
  sprintLabel,
  sprintOptions,
  type OwnerNames,
  type WorkItemFilters,
  type WorkItemNode,
} from './tree';

/** The sprint + assignee the user last picked; null until a filter is touched. */
export interface WorkFilterSelection {
  iteration: string | null;
  assignee: string | null;
}

export const WORK_FILTERS_STORAGE_KEY = 'masterwork.work-filters';

// getOnInit: read localStorage before the first render, so a reload opens on
// the remembered selection instead of flashing the default first.
export const workFilterSelectionAtom = atomWithStorage<WorkFilterSelection | null>(
  WORK_FILTERS_STORAGE_KEY,
  null,
  undefined,
  { getOnInit: true },
);

export const WORK_SOURCES_QUERY_KEY = ['workSources'];
export const WORK_ITEMS_QUERY_KEY = ['workItems'];

/** Env var naming the DevOps PAT. The backend only ever stores this name. */
export const DEFAULT_SECRET_REF = 'AZURE_DEVOPS_PAT';

export const workSourcesQueryAtom = atomWithQuery(() => ({
  queryKey: WORK_SOURCES_QUERY_KEY,
  queryFn: async (): Promise<WorkSource[]> => (await api.work.listWorkSources()).data,
}));

export const workItemsQueryAtom = atomWithQuery(() => ({
  queryKey: WORK_ITEMS_QUERY_KEY,
  queryFn: async (): Promise<WorkItem[]> => (await api.work.listWorkItems()).data,
}));

export const createWorkSourceMutationAtom = atomWithMutation((get) => ({
  mutationFn: (body: WorkSourceCreateRequest): Promise<WorkSource> =>
    api.work.createWorkSource(body).then((r) => r.data),
  onSuccess: () => get(queryClientAtom).invalidateQueries({ queryKey: WORK_SOURCES_QUERY_KEY }),
}));

export const syncWorkSourceMutationAtom = atomWithMutation((get) => ({
  mutationFn: (sourceId: string): Promise<WorkSyncResult> =>
    api.work.syncWorkSource(sourceId, { timeout: WORK_SYNC_TIMEOUT_MS }).then((r) => r.data),
  onSuccess: () => {
    const queryClient = get(queryClientAtom);
    queryClient.invalidateQueries({ queryKey: WORK_ITEMS_QUERY_KEY });
    // The sync moved the source's last_sync_at too.
    queryClient.invalidateQueries({ queryKey: WORK_SOURCES_QUERY_KEY });
  },
}));

export const startWorkItemMutationAtom = atomWithMutation(() => ({
  mutationFn: (itemId: number): Promise<WorkItemStartResponse> =>
    api.work.startWorkItem(itemId).then((r) => r.data),
}));

/** "myorg / widgets" — the source as a human reads it, from its org URL. */
export function sourceLabel(source: WorkSource): string {
  const org = source.org_url.replace(/\/+$/, '').split('/').pop() || source.org_url;
  return `${org} / ${source.project}`;
}

/**
 * True for a plain http(s) link. Item URLs come from DevOps, so they are
 * untrusted input: anything else (`javascript:`…) is rendered as text.
 */
export function isHttpUrl(url: string): boolean {
  return /^https?:\/\//i.test(url);
}
