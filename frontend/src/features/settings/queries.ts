import { atomWithMutation, atomWithQuery, queryClientAtom } from 'jotai-tanstack-query';
import { api } from '~/api/client';
import type { AppSettings, AppSettingsUpdateRequest } from '~/api/generated';

export const APP_SETTINGS_QUERY_KEY = ['appSettings'];

/** The launcher's project picker lists projects_root's folders, so a root change invalidates it. */
export const LAUNCHER_PROJECTS_QUERY_KEY = ['launcherProjects'];

/** projects_root, the effective assistant agent, and every other persisted app setting. */
export const appSettingsQueryAtom = atomWithQuery(() => ({
  queryKey: APP_SETTINGS_QUERY_KEY,
  queryFn: async (): Promise<AppSettings> => (await api.settings.getSettings()).data,
}));

export const updateSettingsMutationAtom = atomWithMutation((get) => ({
  mutationFn: (body: AppSettingsUpdateRequest): Promise<AppSettings> =>
    api.settings.updateSettings(body).then((r) => r.data),
  onSuccess: () => {
    const queryClient = get(queryClientAtom);
    void queryClient.invalidateQueries({ queryKey: LAUNCHER_PROJECTS_QUERY_KEY });
    // Awaited, so the mutation stays pending until the switcher's select can show the saved value.
    return queryClient.invalidateQueries({ queryKey: APP_SETTINGS_QUERY_KEY });
  },
}));
