import { test, expect, type Page, type Route } from '@playwright/experimental-ct-react';
import type { AssetDetail, InstalledSkill, UpstreamCheckResult } from '~/api/generated';
import { SkillDetailApp } from './harness/SkillDetailApp';
import { installedSkill, upstreamCheckResult } from './harness/catalogFixtures';

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET,POST,PUT,PATCH,DELETE,OPTIONS',
  'Access-Control-Allow-Headers': '*',
};

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json',
    headers: CORS,
    body: JSON.stringify(body),
  });
}

const DETAIL: AssetDetail = {
  id: 'claude:skill:frontend-dev',
  kind: 'skill',
  provider: 'claude',
  name: 'frontend-dev',
  title: 'Frontend Dev',
  description: 'React frontend guidelines.',
  model: null,
  agents: ['claude'],
  path: '/Users/me/.claude/skills/frontend-dev/SKILL.md',
  created_at: '2026-08-01T10:00:00Z',
  updated_at: '2026-08-02T10:00:00Z',
  read_only: false,
  content: '---\nname: frontend-dev\n---\n# Frontend Dev\n\nUse React.\n',
};

interface Calls {
  /** Every non-preflight request as "METHOD path", plus "force=<bool>" after an update. */
  list: string[];
}

async function mockDetail(
  page: Page,
  opts: {
    installed?: InstalledSkill[];
    check?: UpstreamCheckResult;
    updateStatus?: number;
  } = {},
): Promise<Calls> {
  const calls: string[] = [];
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    if (request.method() === 'OPTIONS') {
      await route.fulfill({ status: 204, headers: CORS, body: '' });
      return;
    }
    const path = decodeURIComponent(new URL(request.url()).pathname);
    calls.push(`${request.method()} ${path}`);

    if (path === '/api/v1/assets/claude:skill:frontend-dev') return json(route, DETAIL);
    if (path === '/api/v1/skills/installed') return json(route, opts.installed ?? []);
    if (path === '/api/v1/skills/installed/frontend-dev/check') {
      return json(route, opts.check ?? upstreamCheckResult());
    }
    if (path === '/api/v1/skills/installed/frontend-dev/update') {
      const body = request.postDataJSON() as { force?: boolean } | null;
      calls.push(`force=${String(body?.force ?? false)}`);
      if (opts.updateStatus && opts.updateStatus >= 400) {
        return json(route, { detail: 'frontend-dev was edited locally' }, opts.updateStatus);
      }
      return json(route, installedSkill({ drift_status: 'current', upstream_sha: '2222' }));
    }
    if (path.endsWith('/diagram')) return json(route, { detail: 'not found' }, 404);
    return json(route, []);
  });
  return { list: calls };
}

const updates = (calls: Calls) =>
  calls.list.filter((c) => c === 'POST /api/v1/skills/installed/frontend-dev/update');
const checks = (calls: Calls) =>
  calls.list.filter((c) => c === 'POST /api/v1/skills/installed/frontend-dev/check');

test('a hand-written skill has no Upstream card', async ({ mount, page }) => {
  await mockDetail(page, { installed: [] });
  await mount(<SkillDetailApp path="/skills/frontend-dev" />);

  // The page is up (the body rendered) and the installed list has answered.
  await expect(page.getByText('Use React.')).toBeVisible();
  await expect(page.getByRole('region', { name: 'Upstream' })).toHaveCount(0);
});

test('an installed skill shows its source, and is not checked until asked', async ({
  mount,
  page,
}) => {
  const calls = await mockDetail(page, { installed: [installedSkill()] });
  await mount(<SkillDetailApp path="/skills/frontend-dev" />);

  const card = page.getByRole('region', { name: 'Upstream' });
  await expect(card).toContainText('Not checked yet');
  const link = card.getByRole('link', { name: /acme\/frontend-dev/ });
  await expect(link).toHaveAttribute(
    'href',
    'https://github.com/acme/frontend-dev/tree/HEAD/frontend-dev',
  );
  await expect(link).toHaveAttribute('rel', /noopener/);
  // Page load costs no GitHub quota: the check is a click away, never automatic.
  expect(checks(calls)).toHaveLength(0);
  await expect(card.getByRole('button', { name: 'Update from source' })).toHaveCount(0);
});

test('a cached status badges the card before any check', async ({ mount, page }) => {
  await mockDetail(page, {
    installed: [
      installedSkill({ drift_status: 'current', last_checked_at: '2026-09-07T10:00:00Z' }),
    ],
  });
  await mount(<SkillDetailApp path="/skills/frontend-dev" />);

  await expect(page.getByRole('region', { name: 'Upstream' })).toContainText('Up to date');
});

test('checking shows the status, the diff, the other files, and updates on request', async ({
  mount,
  page,
}) => {
  const calls = await mockDetail(page, { installed: [installedSkill()] });
  await mount(<SkillDetailApp path="/skills/frontend-dev" />);

  const card = page.getByRole('region', { name: 'Upstream' });
  await card.getByRole('button', { name: 'Check upstream' }).click();

  await expect(card).toContainText('The source has changed since you installed it.');
  await expect(card).toContainText('Update available');
  await expect(card).toContainText('Clarify the steps');
  await expect(card).toContainText('Aug 15, 2026');
  const diff = card.getByLabel('SKILL.md diff');
  await expect(diff).toContainText('-Use React.');
  await expect(diff).toContainText('+Use React and Vite.');
  // CT ships no Tailwind, so the `uppercase` badge reads as typed here.
  await expect(card).toContainText('added');
  await expect(card).toContainText('reference.md');
  expect(checks(calls)).toHaveLength(1);

  // Nothing local was touched, so the update goes straight through, without force.
  await card.getByRole('button', { name: 'Update from source' }).click();

  await expect(page.getByText('Updated frontend-dev from source')).toBeVisible();
  expect(updates(calls)).toHaveLength(1);
  expect(calls.list).toContain('force=false');
});

test('updating over local edits needs a confirm that names the loss, then forces', async ({
  mount,
  page,
}) => {
  const calls = await mockDetail(page, {
    installed: [installedSkill()],
    check: upstreamCheckResult({ status: 'diverged' }),
  });
  await mount(<SkillDetailApp path="/skills/frontend-dev" />);

  const card = page.getByRole('region', { name: 'Upstream' });
  await card.getByRole('button', { name: 'Check upstream' }).click();
  await expect(card).toContainText('Both sides changed');

  await card.getByRole('button', { name: 'Update from source' }).click();

  // The first press only opens the dialog — nothing has posted yet.
  const dialog = page.getByRole('alertdialog');
  await expect(dialog).toContainText('your local edits are lost');
  expect(updates(calls)).toHaveLength(0);

  await dialog.getByRole('button', { name: 'Keep my edits' }).click();
  await expect(dialog).toHaveCount(0);
  expect(updates(calls)).toHaveLength(0);

  await card.getByRole('button', { name: 'Update from source' }).click();
  await page.getByRole('alertdialog').getByRole('button', { name: 'Overwrite and update' }).click();

  await expect(page.getByText('Updated frontend-dev from source')).toBeVisible();
  expect(updates(calls)).toHaveLength(1);
  expect(calls.list).toContain('force=true');
});

test('a source that vanished is said so, with nothing to update to', async ({ mount, page }) => {
  await mockDetail(page, {
    installed: [installedSkill()],
    check: upstreamCheckResult({
      status: 'unknown_origin',
      upstream_sha: null,
      upstream_last_modified_at: null,
      upstream_last_change_summary: null,
      skill_md_diff: '',
      other_changes: [],
    }),
  });
  await mount(<SkillDetailApp path="/skills/frontend-dev" />);

  const card = page.getByRole('region', { name: 'Upstream' });
  await card.getByRole('button', { name: 'Check upstream' }).click();

  await expect(card).toContainText('The source folder no longer exists upstream.');
  await expect(card.getByRole('button', { name: 'Update from source' })).toHaveCount(0);
});
