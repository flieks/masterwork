import { test, expect } from '@playwright/experimental-ct-react';
import type { Proposal, ProjectUpdate } from '~/api/generated';
import { ProposalCard } from '~/features/chat/components/ProposalCard';
import { TestProviders } from './harness/TestProviders';

function makeProposal(overrides: Partial<Proposal> = {}): Proposal {
  return {
    id: 'p1',
    status: 'pending',
    summary: 'Update the frontend-dev skill',
    changes: [
      {
        path: '/Users/me/.claude/skills/frontend-dev/SKILL.md',
        action: 'update',
        new_content: null,
        description: 'Add a testing section',
        asset_id: 'claude:skill:frontend-dev',
      },
    ],
    error: null,
    project_update: null,
    created_at: '2026-07-16T10:00:00Z',
    ...overrides,
  };
}

function makeProjectUpdate(overrides: Partial<ProjectUpdate> = {}): ProjectUpdate {
  return {
    project_id: 'proj-1',
    name: null,
    goal: null,
    flow_mermaid: null,
    asset_ids: null,
    description: 'Link the deploy skills and set a flow',
    ...overrides,
  };
}

test('pending: shows summary, shortened path, and enabled actions', async ({ mount, page }) => {
  await mount(
    <TestProviders>
      <ProposalCard proposal={makeProposal()} sessionId="s1" />
    </TestProviders>,
  );

  await expect(page.getByText('Update the frontend-dev skill')).toBeVisible();
  await expect(page.getByText('Pending')).toBeVisible();
  await expect(page.getByText('~/.claude/skills/frontend-dev/SKILL.md')).toBeVisible();
  await expect(page.getByRole('button', { name: /Accept/ })).toBeEnabled();
  await expect(page.getByRole('button', { name: /Reject/ })).toBeEnabled();
});

test('applied: shows the applied badge and disables actions', async ({ mount, page }) => {
  await mount(
    <TestProviders>
      <ProposalCard proposal={makeProposal({ status: 'applied' })} sessionId="s1" />
    </TestProviders>,
  );

  await expect(page.getByText('Applied ✓')).toBeVisible();
  await expect(page.getByRole('button', { name: /Accept/ })).toBeDisabled();
  await expect(page.getByRole('button', { name: /Reject/ })).toBeDisabled();
});

test('rejected: shows the rejected badge and disables actions', async ({ mount, page }) => {
  await mount(
    <TestProviders>
      <ProposalCard proposal={makeProposal({ status: 'rejected' })} sessionId="s1" />
    </TestProviders>,
  );

  await expect(page.getByText('Rejected')).toBeVisible();
  await expect(page.getByRole('button', { name: /Accept/ })).toBeDisabled();
});

test('failed: surfaces the error text and stays retryable', async ({ mount, page }) => {
  await mount(
    <TestProviders>
      <ProposalCard
        proposal={makeProposal({
          status: 'failed',
          error: 'Path escapes provider root',
          changes: [
            {
              path: '/Users/me/.claude/skills/frontend-dev/SKILL.md',
              action: 'update',
              new_content: '# updated\n',
              description: 'Add a testing section',
              asset_id: 'claude:skill:frontend-dev',
            },
          ],
        })}
        sessionId="s1"
      />
    </TestProviders>,
  );

  await expect(page.getByText('Failed')).toBeVisible();
  await expect(page.getByText('Path escapes provider root')).toBeVisible();
  await expect(page.getByRole('button', { name: /Retry/ })).toBeEnabled();
  await expect(page.getByRole('button', { name: /Reject/ })).toBeEnabled();
});

test('failed without content: hides Retry — only Reject remains', async ({ mount, page }) => {
  // Default makeProposal change is an update with new_content: null.
  await mount(
    <TestProviders>
      <ProposalCard
        proposal={makeProposal({ status: 'failed', error: 'missing new_content for update' })}
        sessionId="s1"
      />
    </TestProviders>,
  );

  await expect(page.getByText('Failed')).toBeVisible();
  await expect(page.getByText('missing new_content for update')).toBeVisible();
  await expect(page.getByRole('button', { name: /Retry/ })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /Accept/ })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /Reject/ })).toBeEnabled();
});

test('create change exposes a collapsible content preview', async ({ mount, page }) => {
  await mount(
    <TestProviders>
      <ProposalCard
        proposal={makeProposal({
          changes: [
            {
              path: '/Users/me/.claude/agents/reviewer.md',
              action: 'create',
              new_content: '# Reviewer\n',
              description: 'New agent',
              asset_id: null,
            },
          ],
        })}
        sessionId="s1"
      />
    </TestProviders>,
  );

  await expect(page.getByText('CREATE')).toBeVisible();
  await expect(page.getByText('View file content')).toBeVisible();
});

test('project_update: renders only the non-null fields', async ({ mount, page }) => {
  await mount(
    <TestProviders>
      <ProposalCard
        proposal={makeProposal({
          changes: [],
          project_update: makeProjectUpdate({
            name: 'Deploy pipeline',
            asset_ids: ['claude:skill:azure-deploy', 'claude:agent:reviewer'],
          }),
        })}
        sessionId="s1"
      />
    </TestProviders>,
  );

  // Header + description + the set fields (scoped to the project-update region).
  const block = page.getByRole('region', { name: 'Project update' });
  await expect(block).toBeVisible();
  await expect(block.getByText('Link the deploy skills and set a flow')).toBeVisible();
  await expect(block.getByText('Deploy pipeline')).toBeVisible();
  await expect(block.getByText('claude:skill:azure-deploy')).toBeVisible();
  await expect(block.getByText('claude:agent:reviewer')).toBeVisible();

  // Null fields stay hidden.
  await expect(page.getByText('View goal')).toHaveCount(0);
  await expect(page.getByText('Preview diagram')).toHaveCount(0);
});

test('project_update: goal and flow fields appear when present', async ({ mount, page }) => {
  await mount(
    <TestProviders>
      <ProposalCard
        proposal={makeProposal({
          changes: [],
          project_update: makeProjectUpdate({
            goal: '# New goal\n\nShip it.',
            flow_mermaid: 'flowchart TD\n  A --> B',
          }),
        })}
        sessionId="s1"
      />
    </TestProviders>,
  );

  await expect(page.getByText('View goal')).toBeVisible();
  await expect(page.getByText('Preview diagram')).toBeVisible();
  // asset_ids null → "Linked assets" field absent.
  await expect(page.getByText('Linked assets')).toHaveCount(0);
});
