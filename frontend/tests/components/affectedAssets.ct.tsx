import { test, expect } from '@playwright/experimental-ct-react';
import type { ChatMessage, Proposal, ProposalStatus } from '~/api/generated';
import { AffectedAssets } from '~/features/chat/components/AffectedAssets';
import { TestProviders } from './harness/TestProviders';

let nextId = 0;

function messageWithProposal(
  status: ProposalStatus,
  assetIds: (string | null)[],
  overrides: Partial<Proposal> = {},
): ChatMessage {
  const id = `m${++nextId}`;
  return {
    id,
    session_id: 's1',
    role: 'assistant',
    content: 'reply',
    created_at: '2026-07-18T10:00:00Z',
    proposal: {
      id: `p-${id}`,
      status,
      summary: 'change stuff',
      changes: assetIds.map((assetId, i) => ({
        path: `/Users/me/.claude/skills/thing-${i}/SKILL.md`,
        action: 'update',
        new_content: 'x',
        description: 'd',
        asset_id: assetId,
      })),
      project_update: null,
      error: null,
      created_at: '2026-07-18T10:00:00Z',
      ...overrides,
    },
  };
}

test('renders nothing when no proposals reference assets', async ({ mount, page }) => {
  await mount(
    <TestProviders>
      <AffectedAssets
        messages={[
          {
            id: 'm-plain',
            session_id: 's1',
            role: 'user',
            content: 'hi',
            proposal: null,
            created_at: '2026-07-18T10:00:00Z',
          },
          messageWithProposal('pending', [null]),
        ]}
      />
    </TestProviders>,
  );

  await expect(page.getByRole('region', { name: 'Assets in this chat' })).toHaveCount(0);
});

test('dedupes assets across proposals and links to detail pages', async ({ mount, page }) => {
  await mount(
    <TestProviders>
      <AffectedAssets
        messages={[
          messageWithProposal('rejected', ['claude:skill:frontend-dev']),
          messageWithProposal('failed', ['claude:skill:frontend-dev', 'claude:agent:reviewer']),
        ]}
      />
    </TestProviders>,
  );

  const region = page.getByRole('region', { name: 'Assets in this chat' });
  await expect(region.getByRole('link')).toHaveCount(2);
  await expect(region.getByRole('link', { name: 'frontend-dev' })).toHaveAttribute(
    'href',
    '/skills/frontend-dev',
  );
  await expect(region.getByRole('link', { name: 'reviewer' })).toHaveAttribute(
    'href',
    '/agents/reviewer',
  );
});

test('marks assets with applied proposals and carries plugin provider in the URL', async ({
  mount,
  page,
}) => {
  await mount(
    <TestProviders>
      <AffectedAssets
        messages={[
          messageWithProposal('applied', ['claude:skill:frontend-dev']),
          messageWithProposal('pending', ['claude-plugin:skill:vercel:bootstrap']),
        ]}
      />
    </TestProviders>,
  );

  const region = page.getByRole('region', { name: 'Assets in this chat' });
  const applied = region.getByRole('link', { name: /frontend-dev/ });
  await expect(applied.getByLabel('applied')).toBeVisible();

  // Plugin names keep their colons; the provider travels via ?p=.
  const plugin = region.getByRole('link', { name: /vercel:bootstrap/ });
  await expect(plugin).toHaveAttribute('href', '/skills/vercel%3Abootstrap?p=claude-plugin');
  await expect(plugin.getByLabel('applied')).toHaveCount(0);
});
