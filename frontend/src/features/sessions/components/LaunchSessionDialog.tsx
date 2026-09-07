import { useEffect, useState } from 'react';
import { useAtom } from 'jotai';
import { AlertTriangle, FolderOpen } from 'lucide-react';
import type { LaunchMode } from '~/api/generated';
import { Button } from '~/components/ui/button';
import { Input } from '~/components/ui/input';
import { Skeleton } from '~/components/ui/skeleton';
import { Textarea } from '~/components/ui/textarea';
import { toast } from '~/components/ui/sonner';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '~/components/ui/dialog';
import { apiErrorMessage } from '~/api/client';
import { FolderPickerDialog } from '~/components/FolderPickerDialog';
import {
  appSettingsQueryAtom,
  createLauncherProjectMutationAtom,
  launchSessionMutationAtom,
  launcherProjectsQueryAtom,
  updateSettingsMutationAtom,
} from '../queries';

interface LaunchSessionDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/** Starts a factory run against a picked (or freshly created) project folder —
 * always through factory/run.py, never a bare `claude` invocation. */
export function LaunchSessionDialog({ open, onOpenChange }: LaunchSessionDialogProps) {
  const [
    {
      data: appSettings,
      isPending: isSettingsPending,
      isError: isSettingsError,
      refetch: refetchSettings,
    },
  ] = useAtom(appSettingsQueryAtom);
  const [
    {
      data: projects,
      isPending: isProjectsPending,
      isError: isProjectsError,
      refetch: refetchProjects,
    },
  ] = useAtom(launcherProjectsQueryAtom);
  const [{ mutateAsync: saveSettings, isPending: isSavingRoot }] =
    useAtom(updateSettingsMutationAtom);
  const [{ mutateAsync: createProject, isPending: isCreatingProject }] = useAtom(
    createLauncherProjectMutationAtom,
  );
  const [{ mutateAsync: launchSession, isPending: isLaunching }] =
    useAtom(launchSessionMutationAtom);
  const [folderBrowserOpen, setFolderBrowserOpen] = useState(false);

  const [rootDraft, setRootDraft] = useState('');
  const [projectPath, setProjectPath] = useState('');
  const [newFolderName, setNewFolderName] = useState('');
  const [requestText, setRequestText] = useState('');
  const [mode, setMode] = useState<LaunchMode>('autonomous');

  // Re-seed the root field whenever the dialog (re)opens or settings load.
  useEffect(() => {
    if (open && appSettings) setRootDraft(appSettings.projects_root);
  }, [open, appSettings]);

  function reset() {
    setProjectPath('');
    setNewFolderName('');
    setRequestText('');
    setMode('autonomous');
    setFolderBrowserOpen(false);
  }

  async function saveRoot(path?: string) {
    const trimmed = (path ?? rootDraft).trim();
    if (!trimmed) return;
    try {
      await saveSettings({ projects_root: trimmed });
      setRootDraft(trimmed);
      setFolderBrowserOpen(false);
      toast.success('Projects root updated');
    } catch (err) {
      toast.error('Could not update the projects root', { description: apiErrorMessage(err) });
    }
  }

  async function createFolder() {
    const trimmed = newFolderName.trim();
    if (!trimmed) return;
    try {
      const project = await createProject({ name: trimmed });
      setProjectPath(project.path);
      setNewFolderName('');
    } catch (err) {
      toast.error('Could not create the folder', { description: apiErrorMessage(err) });
    }
  }

  const canLaunch = projectPath.length > 0 && requestText.trim().length > 0 && !isLaunching;

  async function launch() {
    if (!canLaunch) return;
    try {
      await launchSession({
        project_path: projectPath,
        request_text: requestText.trim(),
        mode,
      });
      onOpenChange(false);
      reset();
      toast.success('Factory run started', { description: projectPath });
    } catch (err) {
      toast.error('Could not start the run', { description: apiErrorMessage(err) });
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next);
        if (!next) reset();
      }}
    >
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>New session</DialogTitle>
          <DialogDescription>
            Starts a factory pipeline run against a project — always unattended, never a bare chat.
          </DialogDescription>
        </DialogHeader>

        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            void launch();
          }}
        >
          <div className="space-y-1.5">
            <label htmlFor="launch-root" className="text-sm font-medium">
              Projects root
            </label>
            {isSettingsPending ? (
              <Skeleton className="h-9 w-full" />
            ) : isSettingsError ? (
              <p className="flex items-center gap-2 text-sm text-muted-foreground">
                <AlertTriangle className="size-4" /> Couldn't load the projects root.
                <Button type="button" variant="outline" size="sm" onClick={() => void refetchSettings()}>
                  Retry
                </Button>
              </p>
            ) : (
              <div className="flex gap-2">
                <Input
                  id="launch-root"
                  value={rootDraft}
                  onChange={(e) => setRootDraft(e.target.value)}
                  placeholder="/Users/you/Projects"
                />
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => void saveRoot()}
                  disabled={
                    isSavingRoot ||
                    !rootDraft.trim() ||
                    rootDraft.trim() === appSettings?.projects_root
                  }
                >
                  {isSavingRoot ? 'Saving…' : 'Save'}
                </Button>
                <Button type="button" variant="outline" onClick={() => setFolderBrowserOpen(true)}>
                  <FolderOpen className="size-4" /> Browse
                </Button>
              </div>
            )}
            <FolderPickerDialog
              open={folderBrowserOpen}
              onOpenChange={setFolderBrowserOpen}
              initialPath={appSettings?.projects_root ?? null}
              title="Choose the projects root"
              description="Folders are listed from the machine running the backend, not from this browser."
              shortcuts={
                appSettings ? [{ label: 'Current root', path: appSettings.projects_root }] : []
              }
              isConfirming={isSavingRoot}
              onConfirm={(path) => void saveRoot(path)}
            />
          </div>

          <div className="space-y-1.5">
            <label htmlFor="launch-project" className="text-sm font-medium">
              Project
            </label>
            {isProjectsPending ? (
              <Skeleton className="h-9 w-full" />
            ) : isProjectsError ? (
              <p className="flex items-center gap-2 text-sm text-muted-foreground">
                <AlertTriangle className="size-4" /> Couldn't load projects.
                <Button type="button" variant="outline" size="sm" onClick={() => void refetchProjects()}>
                  Retry
                </Button>
              </p>
            ) : (
              <select
                id="launch-project"
                value={projectPath}
                onChange={(e) => setProjectPath(e.target.value)}
                className="h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background"
              >
                <option value="">Select a project…</option>
                {(projects ?? []).map((project) => (
                  <option key={project.path} value={project.path}>
                    {project.name}
                    {project.is_git_repo ? '' : ' (not a git repo)'}
                  </option>
                ))}
              </select>
            )}

            <div className="flex gap-2">
              <Input
                aria-label="New folder name"
                value={newFolderName}
                onChange={(e) => setNewFolderName(e.target.value)}
                placeholder="New folder name"
              />
              <Button
                type="button"
                variant="outline"
                onClick={() => void createFolder()}
                disabled={isCreatingProject || !newFolderName.trim()}
              >
                {isCreatingProject ? 'Creating…' : 'Create'}
              </Button>
            </div>
          </div>

          <div className="space-y-1.5">
            <label htmlFor="launch-request" className="text-sm font-medium">
              Request
            </label>
            <Textarea
              id="launch-request"
              value={requestText}
              onChange={(e) => setRequestText(e.target.value)}
              placeholder="What should the factory build?"
              rows={5}
              required
            />
          </div>

          <fieldset className="space-y-2" role="radiogroup" aria-label="Mode">
            <legend className="text-sm font-medium">Mode</legend>
            <label className="flex items-start gap-2 text-sm">
              <input
                type="radio"
                name="launch-mode"
                className="mt-1"
                checked={mode === 'autonomous'}
                onChange={() => setMode('autonomous')}
              />
              Fully autonomous: plan and build with best-guess assumptions, never ask me
            </label>
            <label className="flex items-start gap-2 text-sm">
              <input
                type="radio"
                name="launch-mode"
                className="mt-1"
                checked={mode === 'interview'}
                onChange={() => setMode('interview')}
              />
              Interview me: pause on weak assumptions before building
            </label>
          </fieldset>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={!canLaunch}>
              {isLaunching ? 'Launching…' : 'Launch'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
