import { useAtom } from 'jotai';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { ArrowLeft, AlertTriangle } from 'lucide-react';
import { Skeleton } from '~/components/ui/skeleton';
import { EmptyState } from '~/components/EmptyState';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '~/components/ui/tabs';
import { apiErrorMessage } from '~/api/client';
import { projectQueryAtom } from '../queries';
import { ProjectOverviewTab } from './ProjectOverviewTab';
import { ProjectChatTab } from './ProjectChatTab';
import { ProjectSimulationTab } from './ProjectSimulationTab';
import { ProjectSummaryTab } from './ProjectSummaryTab';
import { ProjectTriggerTab } from './ProjectTriggerTab';
import { ProjectGeneralityTab } from './ProjectGeneralityTab';

const TABS = ['overview', 'chat', 'simulation', 'summary', 'trigger', 'generality'] as const;
type Tab = (typeof TABS)[number];

function isTab(value: string | null): value is Tab {
  return TABS.includes(value as Tab);
}

export function ProjectDetailPage() {
  const { id = '' } = useParams();
  const [{ data: project, isPending, isError, error }] = useAtom(projectQueryAtom(id));
  const [searchParams, setSearchParams] = useSearchParams();

  const tab: Tab = isTab(searchParams.get('tab')) ? (searchParams.get('tab') as Tab) : 'overview';

  function setTab(next: string) {
    setSearchParams(
      (prev) => {
        const params = new URLSearchParams(prev);
        params.set('tab', next);
        return params;
      },
      { replace: true },
    );
  }

  if (isPending) {
    return (
      <div className="mx-auto w-full max-w-4xl space-y-4 p-6">
        <Skeleton className="h-4 w-28" />
        <Skeleton className="h-9 w-64" />
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }

  if (isError || !project) {
    const notFound = apiErrorMessage(error).toLowerCase().includes('not found');
    return (
      <div className="mx-auto w-full max-w-4xl p-6">
        <BackLink />
        <EmptyState
          className="mt-4"
          icon={<AlertTriangle className="size-8" />}
          title={notFound ? 'Project not found' : "Couldn't load project"}
          description={notFound ? id : apiErrorMessage(error)}
        />
      </div>
    );
  }

  return (
    <Tabs value={tab} onValueChange={setTab} className="flex h-full min-h-0 flex-col">
      <div className="flex flex-col gap-3 border-b px-6 pb-3 pt-5">
        <BackLink />
        <TabsList className="self-start">
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="chat">Chat</TabsTrigger>
          <TabsTrigger value="simulation">Simulation</TabsTrigger>
          <TabsTrigger value="summary">Summary</TabsTrigger>
          <TabsTrigger value="trigger">Trigger</TabsTrigger>
          <TabsTrigger value="generality">Generality</TabsTrigger>
        </TabsList>
      </div>

      <TabsContent value="overview" className="min-h-0 flex-1 overflow-y-auto">
        <ProjectOverviewTab project={project} />
      </TabsContent>
      <TabsContent value="chat" className="min-h-0 flex-1">
        <ProjectChatTab projectId={project.id} />
      </TabsContent>
      <TabsContent value="simulation" className="min-h-0 flex-1">
        <ProjectSimulationTab project={project} />
      </TabsContent>
      <TabsContent value="summary" className="min-h-0 flex-1 overflow-y-auto">
        <ProjectSummaryTab project={project} />
      </TabsContent>
      <TabsContent value="trigger" className="min-h-0 flex-1 overflow-y-auto">
        <ProjectTriggerTab project={project} />
      </TabsContent>
      <TabsContent value="generality" className="min-h-0 flex-1 overflow-y-auto">
        <ProjectGeneralityTab project={project} />
      </TabsContent>
    </Tabs>
  );
}

function BackLink() {
  return (
    <Link
      to="/projects"
      className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
    >
      <ArrowLeft className="size-4" /> Back to projects
    </Link>
  );
}
