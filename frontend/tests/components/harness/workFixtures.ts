import type { WorkItem, WorkItemStartResponse, WorkSource } from '~/api/generated';

export const SOURCE_ID = '6f3b2c1a-0d4e-4a9b-8c77-1f2e3d4c5b6a';

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
    external_url: 'https://dev.azure.com/acme/widgets/_workitems/edit/4821',
    item_type: 'User Story',
    title: 'Operators can retry a failed ingest run',
    description_md: 'The retry button reruns the pipeline from the failed stage.',
    acceptance_md: '- Retry is disabled while a run is in flight',
    state: 'Active',
    iteration: 'widgets\\Sprint 14',
    priority: 2,
    tags: ['ingest'],
    external_changed_at: '2026-08-12T14:00:00Z',
    synced_at: '2026-08-13T09:15:00Z',
    ...overrides,
  };
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
