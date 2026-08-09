import { test, expect } from '@playwright/experimental-ct-react';
import { RunCard } from '~/features/sessions/components/RunCard';
import { SessionAssets } from '~/features/sessions/components/SessionAssets';
import { groupAssetsByLane, usesBarPct, assetUsePath } from '~/features/sessions/assets';
import { TestProviders } from './harness/TestProviders';
import {
  assetUsageRows,
  assetUse,
  chatRun,
  factoryRunSummary,
  RUN_END,
} from './harness/runFixtures';

/**
 * Asset attribution is why the screen exists: which skills and agents a run
 * used, and which of them belongs to which lane.
 */

const NOW = Date.parse(RUN_END) + 60_000;

test('the detail lists every asset under the lane that used it, each one a link', async ({
  mount,
  page,
}) => {
  await mount(
    <TestProviders>
      <SessionAssets session={chatRun()} />
    </TestProviders>,
  );

  await expect(page.getByRole('heading', { name: 'Assets used' })).toBeVisible();

  // The lane is what makes this attribution rather than a list: the skill the
  // main lane loaded and the agent it dispatched are visibly different rows.
  await expect(page.getByText('main', { exact: true })).toBeVisible();
  await expect(page.getByText('backend-developer', { exact: true }).first()).toBeVisible();
  await expect(page.getByText('no lane')).toBeVisible();

  // A skill chip points at the skill page, an agent chip at the agent page.
  await expect(page.getByRole('link', { name: /agent-factory/ })).toHaveAttribute(
    'href',
    '/skills/agent-factory',
  );
  await expect(page.getByRole('link', { name: /general-purpose/ })).toHaveAttribute(
    'href',
    '/agents/general-purpose',
  );

  // Repeat uses are counted on the chip.
  await expect(page.getByRole('link', { name: /agent-factory/ })).toContainText('×2');
});

test('the unresolved subagent bucket is shown, labelled, and not a link', async ({
  mount,
  page,
}) => {
  await mount(
    <TestProviders>
      <SessionAssets session={chatRun()} />
    </TestProviders>,
  );

  const chip = page.getByText('subagent', { exact: true }).locator('..');
  await expect(chip).toBeVisible();
  // Nowhere to send the user: there is no agent by that name.
  await expect(page.getByRole('link', { name: /subagent/ })).toHaveCount(0);
  await expect(chip).toHaveAttribute('title', /Claude Code deletes subagent transcripts/);
  await expect(chip).toHaveClass(/border-dashed/);
});

test('a run that used nothing says so rather than showing an empty box', async ({
  mount,
  page,
}) => {
  await mount(
    <TestProviders>
      <SessionAssets session={chatRun({ assets: [] })} />
    </TestProviders>,
  );

  await expect(page.getByText(/No skill or agent was recorded/)).toBeVisible();
});

test('cards carry a capped row of asset chips, and never nest an anchor', async ({
  mount,
  page,
}) => {
  const chat = chatRun();
  await mount(
    <TestProviders>
      <RunCard
        session={{ ...chat, phases: chat.phases.map((p) => ({ ...p })) }}
        now={Date.parse('2026-08-08T09:04:10.000Z')}
      />
    </TestProviders>,
  );

  const row = page.getByLabel('Assets used');
  await expect(row).toContainText('subagent');
  await expect(row).toContainText('agent-factory');
  // Five assets, four shown.
  await expect(row).toContainText('+1 more');

  // The card is the only link — chips inside it are plain spans, because an
  // anchor inside an anchor is invalid HTML that browsers silently unnest.
  await expect(page.getByRole('link')).toHaveCount(1);
});

test('a card with no recorded assets shows no chip row', async ({ mount, page }) => {
  await mount(
    <TestProviders>
      <RunCard session={factoryRunSummary({ assets: [] })} now={NOW} />
    </TestProviders>,
  );
  await expect(page.getByLabel('Assets used')).toHaveCount(0);
});

test('grouping keeps the API order and gathers each lane once', () => {
  const groups = groupAssetsByLane([
    assetUse('agent', 'subagent', 7, null),
    assetUse('skill', 'agent-factory', 2, 'main'),
    assetUse('agent', 'backend-developer', 3, 'backend-developer'),
    assetUse('skill', 'frontend-dev', 1, 'main'),
  ]);

  expect(groups.map((g) => g.lane)).toEqual([null, 'main', 'backend-developer']);
  expect(groups[1].assets.map((a) => a.name)).toEqual(['agent-factory', 'frontend-dev']);
});

test('an asset path follows the provider convention, and unresolved has none', () => {
  expect(assetUsePath(assetUse('skill', 'frontend-dev', 1, null))).toBe('/skills/frontend-dev');
  expect(assetUsePath(assetUse('agent', 'general-purpose', 1, null))).toBe(
    '/agents/general-purpose',
  );
  // Plugin assets carry their provider in `?p=`, exactly as the asset pages expect.
  expect(assetUsePath({ kind: 'skill', name: 'docx', asset_id: 'claude-plugin:skill:docx' })).toBe(
    '/skills/docx?p=claude-plugin',
  );
  expect(assetUsePath(assetUse('agent', 'subagent', 78, null))).toBeNull();
});

test('the uses bar is scaled to real names, not to the unresolved bucket', () => {
  const rows = assetUsageRows();
  const busiestReal = rows.find((r) => r.name === 'agent-factory')!;
  const unresolved = rows.find((r) => r.name === 'subagent')!;
  const smallest = rows.find((r) => r.name === 'restart-backend')!;

  // 78 uses would flatten every real skill to a sliver if it set the scale.
  expect(usesBarPct(busiestReal, rows)).toBe(100);
  expect(usesBarPct(smallest, rows)).toBe(25);
  // The bucket itself is not hidden — just capped.
  expect(usesBarPct(unresolved, rows)).toBe(100);
});
