import { test, expect, type Page, type Route } from '@playwright/experimental-ct-react';
import type { AgentId, AppSettings, InstructionsDoc } from '~/api/generated';
import { InstructionsApp } from './harness/AgentHarness';

/** Per-agent global instructions (v1.48): CLAUDE.md for Claude Code, AGENTS.md for Codex. */

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET,POST,PATCH,PUT,DELETE,OPTIONS',
  'Access-Control-Allow-Headers': '*',
};

function json(route: Route, status: number, body: unknown) {
  return route.fulfill({
    status,
    contentType: 'application/json',
    headers: CORS,
    body: JSON.stringify(body),
  });
}

function settings(active: AgentId): AppSettings {
  return {
    projects_root: '/Users/test/Projects',
    assistant_agent: active,
    agents: [
      { id: 'claude', label: 'Claude Code', installed: true, bin_path: '/usr/local/bin/claude' },
      { id: 'codex', label: 'Codex', installed: true, bin_path: '/opt/homebrew/bin/codex' },
    ],
  };
}

function claudeDoc(over: Partial<InstructionsDoc> = {}): InstructionsDoc {
  return {
    agent: 'claude',
    file_name: 'CLAUDE.md',
    path: '/Users/test/.claude/CLAUDE.md',
    content: '# Claude rules\n\nBe brief.',
    exists: true,
    updated_at: '2026-09-01T10:00:00Z',
    shadowed_by: null,
    same_file_as: null,
    ...over,
  };
}

function codexDoc(over: Partial<InstructionsDoc> = {}): InstructionsDoc {
  return {
    agent: 'codex',
    file_name: 'AGENTS.md',
    path: '/Users/test/.codex/AGENTS.md',
    content: '# Codex rules',
    exists: true,
    updated_at: '2026-09-01T10:00:00Z',
    shadowed_by: null,
    same_file_as: null,
    ...over,
  };
}

async function mockInstructions(
  page: Page,
  opts: { active: AgentId; claude?: InstructionsDoc; codex?: InstructionsDoc },
) {
  const requestedAgents: (string | null)[] = [];
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    if (request.method() === 'OPTIONS') {
      await route.fulfill({ status: 204, headers: CORS, body: '' });
      return;
    }
    const url = new URL(request.url());
    if (url.pathname.endsWith('/settings') && request.method() === 'GET') {
      await json(route, 200, settings(opts.active));
      return;
    }
    if (url.pathname.endsWith('/instructions') && request.method() === 'GET') {
      const agent = url.searchParams.get('agent');
      requestedAgents.push(agent);
      await json(
        route,
        200,
        agent === 'codex' ? (opts.codex ?? codexDoc()) : (opts.claude ?? claudeDoc()),
      );
      return;
    }
    await json(route, 404, { detail: `unexpected ${request.method()} ${url.pathname}` });
  });
  return { requestedAgents };
}

test('with no ?agent the page opens on the active assistant and asks for its file', async ({
  mount,
  page,
}) => {
  const { requestedAgents } = await mockInstructions(page, { active: 'codex' });
  await mount(<InstructionsApp />);

  await expect(page.getByRole('heading', { name: 'AGENTS.md' })).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Codex · AGENTS.md' })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  await expect(page.getByText('~/.codex/AGENTS.md')).toBeVisible();
  expect(requestedAgents).toEqual(['codex']);
});

test('a non-empty AGENTS.override.md is flagged as winning over the edited file', async ({
  mount,
  page,
}) => {
  await mockInstructions(page, {
    active: 'claude',
    codex: codexDoc({ shadowed_by: '/Users/test/.codex/AGENTS.override.md' }),
  });
  await mount(<InstructionsApp path="/instructions?agent=codex" />);

  const banner = page.getByRole('alert');
  await expect(banner).toContainText('AGENTS.override.md takes precedence.');
  await expect(banner).toContainText('~/.codex/AGENTS.override.md');
  await expect(banner).toContainText('edits here have no effect until it is removed or emptied');
});

test('one file behind both agents is called out on the tab', async ({ mount, page }) => {
  await mockInstructions(page, {
    active: 'claude',
    claude: claudeDoc({ same_file_as: '/Users/test/.codex/AGENTS.md' }),
  });
  await mount(<InstructionsApp path="/instructions?agent=claude" />);

  await expect(page.getByRole('heading', { name: 'CLAUDE.md' })).toBeVisible();
  await expect(page.getByText(/Same file as/)).toContainText(
    'Same file as ~/.codex/AGENTS.md — edits here apply to both Claude Code and Codex.',
  );
  await expect(page.getByRole('alert')).toHaveCount(0);
});

test('an agent without a global file gets its own empty state', async ({ mount, page }) => {
  await mockInstructions(page, {
    active: 'claude',
    codex: codexDoc({ exists: false, content: '', updated_at: null }),
  });
  await mount(<InstructionsApp path="/instructions?agent=codex" />);

  await expect(page.getByText('No global AGENTS.md yet')).toBeVisible();
  await expect(
    page.getByText('Create one to give every Codex session standing instructions.'),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: 'Create file' })).toBeVisible();
});

test('switching tabs with unsaved edits asks first, and Discard moves on', async ({
  mount,
  page,
}) => {
  await mockInstructions(page, { active: 'claude' });
  await mount(<InstructionsApp />);

  await expect(page.getByRole('heading', { name: 'CLAUDE.md' })).toBeVisible();
  await page.getByRole('button', { name: 'Edit' }).click();
  await page.locator('.cm-content').click();
  await page.keyboard.type(' More.');
  await expect(page.getByRole('button', { name: 'Save' })).toBeEnabled();

  const codexTab = page.getByRole('tab', { name: 'Codex · AGENTS.md' });
  await codexTab.click();
  const dialog = page.getByRole('alertdialog', { name: 'Discard unsaved changes?' });
  await expect(dialog).toBeVisible();

  await dialog.getByRole('button', { name: 'Keep editing' }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByRole('heading', { name: 'CLAUDE.md' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Save' })).toBeEnabled();

  await codexTab.click();
  await page.getByRole('alertdialog').getByRole('button', { name: 'Discard' }).click();
  await expect(page.getByRole('heading', { name: 'AGENTS.md' })).toBeVisible();
  await expect(codexTab).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByRole('button', { name: 'Edit' })).toBeVisible();
});

test('a clean tab switch needs no confirmation', async ({ mount, page }) => {
  await mockInstructions(page, { active: 'claude' });
  await mount(<InstructionsApp />);

  await expect(page.getByRole('heading', { name: 'CLAUDE.md' })).toBeVisible();
  await page.getByRole('tab', { name: 'Codex · AGENTS.md' }).click();
  await expect(page.getByRole('heading', { name: 'AGENTS.md' })).toBeVisible();
  await expect(page.getByRole('alertdialog')).toHaveCount(0);
});
