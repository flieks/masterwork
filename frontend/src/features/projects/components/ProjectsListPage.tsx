import { useState } from 'react';
import { useAtom } from 'jotai';
import { Plus, FolderOpen, AlertTriangle } from 'lucide-react';
import { Badge } from '~/components/ui/badge';
import { Button } from '~/components/ui/button';
import { Card, CardContent, CardHeader } from '~/components/ui/card';
import { Skeleton } from '~/components/ui/skeleton';
import { EmptyState } from '~/components/EmptyState';
import { apiErrorMessage } from '~/api/client';
import { projectsQueryAtom } from '../queries';
import { ProjectCard } from './ProjectCard';
import { NewProjectDialog } from './NewProjectDialog';

export function ProjectsListPage() {
  const [{ data, isPending, isError, error, refetch }] = useAtom(projectsQueryAtom);
  const [dialogOpen, setDialogOpen] = useState(false);

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-5 p-6">
      <header className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-2">
            <h1 className="text-2xl font-semibold tracking-tight">Projects</h1>
            {data ? (
              <Badge variant="muted" aria-label={`${data.length} projects`}>
                {data.length}
              </Badge>
            ) : null}
          </div>
          <p className="text-sm text-muted-foreground">
            Scenarios your skills and agents support — with a goal, linked assets, and a flow
            diagram.
          </p>
        </div>
        <Button onClick={() => setDialogOpen(true)}>
          <Plus /> New project
        </Button>
      </header>

      {isPending ? (
        <ProjectGridSkeleton />
      ) : isError ? (
        <EmptyState
          icon={<AlertTriangle className="size-8" />}
          title="Couldn't load projects"
          description={apiErrorMessage(error)}
          action={
            <Button variant="outline" size="sm" onClick={() => refetch()}>
              Retry
            </Button>
          }
        />
      ) : data.length === 0 ? (
        <EmptyState
          icon={<FolderOpen className="size-8" />}
          title="No projects yet"
          description="Create a project to describe a scenario and let the project chat propose the skills and flow to support it."
          action={
            <Button size="sm" onClick={() => setDialogOpen(true)}>
              <Plus /> New project
            </Button>
          }
        />
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {data.map((project) => (
            <ProjectCard key={project.id} project={project} />
          ))}
        </div>
      )}

      <NewProjectDialog open={dialogOpen} onOpenChange={setDialogOpen} />
    </div>
  );
}

function ProjectGridSkeleton() {
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {Array.from({ length: 6 }).map((_, i) => (
        <Card key={i}>
          <CardHeader>
            <Skeleton className="h-5 w-2/3" />
          </CardHeader>
          <CardContent className="space-y-3">
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-4/5" />
            <div className="flex items-center justify-between pt-1">
              <Skeleton className="h-5 w-16" />
              <Skeleton className="h-4 w-20" />
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
