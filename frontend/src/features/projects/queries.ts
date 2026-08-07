import { atomFamily } from 'jotai/utils';
import { atomWithQuery, atomWithMutation } from 'jotai-tanstack-query';
import { api, GENERATE_TIMEOUT_MS } from '~/api/client';
import type {
  Project,
  ProjectCreateRequest,
  ProjectGeneralityResponse,
  ProjectSuggestLinksResponse,
  ProjectSummaryResponse,
  ProjectTriggerResponse,
  ProjectUpdateRequest,
} from '~/api/generated';

export function projectDetailPath(id: string): string {
  return `/projects/${id}`;
}

export const projectsQueryAtom = atomWithQuery(() => ({
  queryKey: ['projects'],
  queryFn: async () => (await api.projects.listProjects()).data,
}));

export const projectQueryAtom = atomFamily((projectId: string) =>
  atomWithQuery(() => ({
    queryKey: ['project', projectId],
    queryFn: async () => (await api.projects.getProject(projectId)).data,
    enabled: projectId.length > 0,
  })),
);

export const crossChangesQueryAtom = atomFamily((projectId: string) =>
  atomWithQuery(() => ({
    queryKey: ['cross-changes', projectId],
    queryFn: async () => (await api.projects.listProjectCrossChanges(projectId)).data,
    enabled: projectId.length > 0,
  })),
);

export const createProjectMutationAtom = atomWithMutation(() => ({
  mutationFn: (body: ProjectCreateRequest): Promise<Project> =>
    api.projects.createProject(body).then((r) => r.data),
}));

export const updateProjectMutationAtom = atomWithMutation(() => ({
  mutationFn: (vars: { projectId: string; body: ProjectUpdateRequest }): Promise<Project> =>
    api.projects.updateProject(vars.projectId, vars.body).then((r) => r.data),
}));

export const suggestLinksMutationAtom = atomWithMutation(() => ({
  // Reads shortlisted asset files before answering — no client timeout.
  mutationFn: (projectId: string): Promise<ProjectSuggestLinksResponse> =>
    api.projects
      .suggestProjectLinks(projectId, { timeout: GENERATE_TIMEOUT_MS })
      .then((r) => r.data),
}));

export const generateSummaryMutationAtom = atomWithMutation(() => ({
  // One-shot claude -p over the change log — no client timeout, same as scenarios.
  mutationFn: (projectId: string): Promise<ProjectSummaryResponse> =>
    api.projects
      .generateProjectSummary(projectId, { timeout: GENERATE_TIMEOUT_MS })
      .then((r) => r.data),
}));

export const generateTriggerMutationAtom = atomWithMutation(() => ({
  // Reads every linked asset file before answering — no client timeout.
  mutationFn: (projectId: string): Promise<ProjectTriggerResponse> =>
    api.projects
      .generateProjectTrigger(projectId, { timeout: GENERATE_TIMEOUT_MS })
      .then((r) => r.data),
}));

export const auditGeneralityMutationAtom = atomWithMutation(() => ({
  // Reads every linked asset file before answering — no client timeout.
  mutationFn: (projectId: string): Promise<ProjectGeneralityResponse> =>
    api.projects
      .auditProjectGenerality(projectId, { timeout: GENERATE_TIMEOUT_MS })
      .then((r) => r.data),
}));

export const deleteProjectMutationAtom = atomWithMutation(() => ({
  mutationFn: (projectId: string): Promise<void> =>
    api.projects.deleteProject(projectId).then(() => undefined),
}));
