import { createBrowserRouter, Navigate } from 'react-router-dom';
import { AssetDetailPage, AssetListPage } from '~/features/assets';
import { ChatPage } from '~/features/chat';
import { InstructionsPage } from '~/features/instructions';
import { ProjectsListPage, ProjectDetailPage } from '~/features/projects';
import { SessionsListPage, SessionDetailPage } from '~/features/sessions';
import { WorkBacklogPage } from '~/features/work';
import { Layout } from './Layout';
import { NotFound } from './NotFound';

export const router = createBrowserRouter([
  {
    path: '/',
    element: <Layout />,
    children: [
      { index: true, element: <Navigate to="/skills" replace /> },
      { path: 'skills', element: <AssetListPage kind="skill" /> },
      { path: 'skills/:name', element: <AssetDetailPage kind="skill" /> },
      { path: 'agents', element: <AssetListPage kind="agent" /> },
      { path: 'agents/:name', element: <AssetDetailPage kind="agent" /> },
      { path: 'projects', element: <ProjectsListPage /> },
      { path: 'projects/:id', element: <ProjectDetailPage /> },
      { path: 'work', element: <WorkBacklogPage /> },
      { path: 'sessions', element: <SessionsListPage /> },
      { path: 'sessions/:id', element: <SessionDetailPage /> },
      { path: 'instructions', element: <InstructionsPage /> },
      { path: 'chat', element: <ChatPage /> },
      { path: 'chat/:sessionId', element: <ChatPage /> },
      { path: '*', element: <NotFound /> },
    ],
  },
]);
