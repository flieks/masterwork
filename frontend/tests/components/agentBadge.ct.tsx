import { test, expect } from '@playwright/experimental-ct-react';
import { AgentBadge } from '~/features/sessions/components/AgentBadge';
import { RunCard } from '~/features/sessions/components/RunCard';
import { SessionHeader } from '~/features/sessions/components/SessionHeader';
import { TestProviders } from './harness/TestProviders';
import { chatRun } from './harness/runFixtures';

/** Which agent recorded a run, on the card and on the detail header. */

test('a Claude Code session is badged by name', async ({ mount }) => {
  const badge = await mount(<AgentBadge source="claude-code" />);
  await expect(badge).toHaveText('Claude Code');
  await expect(badge).toHaveAttribute('data-agent', 'claude-code');
});

test('a Codex session is badged by name', async ({ mount }) => {
  const badge = await mount(<AgentBadge source="codex" />);
  await expect(badge).toHaveText('Codex');
  await expect(badge).toHaveAttribute('title', 'Recorded by Codex');
});

test('an agent the UI has not met keeps its id rather than vanishing', async ({ mount }) => {
  const badge = await mount(<AgentBadge source="cursor" />);
  await expect(badge).toHaveText('cursor');
});

test('the run card and the header both carry the badge', async ({ mount, page }) => {
  const codex = chatRun({ source: 'codex', model: 'gpt-5-codex' });

  await mount(
    <TestProviders>
      <RunCard session={codex} />
      <SessionHeader session={codex} />
    </TestProviders>,
  );

  await expect(page.locator('[data-agent="codex"]')).toHaveCount(2);
  await expect(page.locator('[data-agent="codex"]').first()).toHaveText('Codex');
});
