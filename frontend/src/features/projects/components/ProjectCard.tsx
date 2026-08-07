import { useAtom } from 'jotai';
import { Link } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { Trash2, Boxes } from 'lucide-react';
import type { Project } from '~/api/generated';
import { Badge } from '~/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '~/components/ui/card';
import { toast } from '~/components/ui/sonner';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '~/components/ui/alert-dialog';
import { apiErrorMessage } from '~/api/client';
import { relativeTime } from '~/lib/datetime';
import { deleteProjectMutationAtom, projectDetailPath } from '../queries';

export function ProjectCard({ project }: { project: Project }) {
  const [{ mutateAsync: remove }] = useAtom(deleteProjectMutationAtom);
  const queryClient = useQueryClient();

  const count = project.asset_ids.length;

  async function onDelete() {
    try {
      await remove(project.id);
      queryClient.invalidateQueries({ queryKey: ['projects'] });
      queryClient.removeQueries({ queryKey: ['project', project.id] });
      // The project's chat sessions were cascaded server-side.
      queryClient.invalidateQueries({ queryKey: ['chatSessions'] });
      toast.success('Project deleted');
    } catch (err) {
      toast.error('Delete failed', { description: apiErrorMessage(err) });
    }
  }

  return (
    <div className="group relative">
      <Link
        to={projectDetailPath(project.id)}
        className="block rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <Card className="h-full transition-colors hover:border-ring hover:bg-accent/40">
          <CardHeader>
            <CardTitle className="truncate pr-8" title={project.name}>
              {project.name}
            </CardTitle>
          </CardHeader>
          <CardContent className="flex h-full flex-col gap-3">
            <p className="line-clamp-3 min-h-[2.5rem] text-sm text-muted-foreground">
              {project.goal.trim() ? project.goal : <span className="italic">No goal yet</span>}
            </p>
            <div className="mt-auto flex items-center justify-between pt-1">
              <Badge variant="muted" className="gap-1">
                <Boxes className="size-3" />
                {count} asset{count === 1 ? '' : 's'}
              </Badge>
              <span className="text-xs text-muted-foreground">
                {relativeTime(project.updated_at)}
              </span>
            </div>
          </CardContent>
        </Card>
      </Link>

      <AlertDialog>
        <AlertDialogTrigger asChild>
          <button
            type="button"
            aria-label={`Delete project ${project.name}`}
            className="absolute right-2 top-2 flex size-7 items-center justify-center rounded-md text-muted-foreground opacity-0 transition hover:bg-background hover:text-destructive focus-visible:opacity-100 group-hover:opacity-100"
          >
            <Trash2 className="size-4" />
          </button>
        </AlertDialogTrigger>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this project?</AlertDialogTitle>
            <AlertDialogDescription>
              “{project.name}” will be permanently removed, along with all of its chat sessions.
              This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={onDelete}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
