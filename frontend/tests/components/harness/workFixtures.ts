import type { WorkItem, WorkItemStartResponse, WorkSource } from '~/api/generated';

export const SOURCE_ID = '6f3b2c1a-0d4e-4a9b-8c77-1f2e3d4c5b6a';

export const SPRINT_33 = 'widgets\\2026 Q3.3';
export const SPRINT_34 = 'widgets\\2026 Q3.4';

/** A registered Azure DevOps project in whichever state a test needs. */
export function workSource(overrides: Partial<WorkSource> = {}): WorkSource {
  return {
    id: SOURCE_ID,
    provider: 'azuredevops',
    org_url: 'https://dev.azure.com/acme',
    project: 'widgets',
    team: null,
    query_wiql: null,
    secret_ref: 'AZURE_DEVOPS_PAT',
    current_iteration: null,
    last_sync_at: '2026-08-13T09:15:00Z',
    created_at: '2026-08-01T08:00:00Z',
    updated_at: '2026-08-13T09:15:00Z',
    ...overrides,
  };
}

export function workItem(overrides: Partial<WorkItem> = {}): WorkItem {
  return {
    id: 1,
    source_id: SOURCE_ID,
    external_id: 4821,
    parent_external_id: null,
    pulled_as_parent: false,
    external_url: 'https://dev.azure.com/acme/widgets/_workitems/edit/4821',
    item_type: 'User Story',
    title: 'Operators can retry a failed ingest run',
    description_md: 'The retry button reruns the pipeline from the failed stage.',
    acceptance_md: '- Retry is disabled while a run is in flight',
    state: 'Active',
    iteration: SPRINT_33,
    assigned_to: 'Alex Doe',
    priority: 2,
    tags: ['ingest'],
    external_changed_at: '2026-08-12T14:00:00Z',
    synced_at: '2026-08-13T09:15:00Z',
    ...overrides,
  };
}

/**
 * The shapes the backlog has to render, in one list:
 * a story with two child tasks; a context Feature (`pulled_as_parent`) that was
 * only fetched to parent an assigned task; and a task whose parent was never
 * fetched, which has to stay top-level rather than vanish.
 */
export function workItemTree(): WorkItem[] {
  return [
    workItem(),
    workItem({
      id: 2,
      external_id: 4822,
      parent_external_id: 4821,
      item_type: 'Task',
      title: 'Wire the retry button to the API',
      description_md: 'Call POST /runs/{id}/retry and refetch the run.',
      acceptance_md: null,
      state: 'Active',
      priority: 2,
      tags: null,
    }),
    workItem({
      id: 3,
      external_id: 4823,
      parent_external_id: 4821,
      item_type: 'Task',
      title: 'Cover retry in the run E2E',
      description_md: 'Extend the ingest spec with a failing-then-retried run.',
      acceptance_md: null,
      state: 'New',
      priority: 3,
      tags: null,
    }),
    // Someone else's Feature: present only so its assigned child has a group.
    workItem({
      id: 4,
      external_id: 4900,
      pulled_as_parent: true,
      item_type: 'Feature',
      title: 'Ingest reliability',
      assigned_to: 'Sam Owner',
      description_md: 'Umbrella for the 2026 Q3 ingest hardening work.',
      acceptance_md: null,
      state: 'In Progress',
      iteration: null,
      priority: null,
      tags: null,
    }),
    workItem({
      id: 5,
      external_id: 4901,
      parent_external_id: 4900,
      item_type: 'Task',
      title: 'Backfill the ingest audit log',
      description_md: 'One-off replay over the last 90 days.',
      acceptance_md: null,
      state: 'New',
      iteration: SPRINT_34,
      priority: 1,
      tags: null,
    }),
    // Parent 9999 was never fetched — the row stays at the top level.
    workItem({
      id: 6,
      external_id: 4950,
      parent_external_id: 9999,
      item_type: 'Bug',
      title: 'Bump the ingest SDK',
      description_md: 'The pinned version drops rows over 4 MB.',
      acceptance_md: null,
      state: 'New',
      iteration: SPRINT_34,
      priority: null,
      tags: null,
    }),
  ];
}

export function startResponse(
  overrides: Partial<WorkItemStartResponse> = {},
): WorkItemStartResponse {
  return {
    prompt: '# 4821 — Operators can retry a failed ingest run\n\nWork this item to completion.',
    launched: false,
    session_id: null,
    link_id: 77,
    ...overrides,
  };
}
