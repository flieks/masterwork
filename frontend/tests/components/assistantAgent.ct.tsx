import { test, expect, type Page, type Route } from '@playwright/experimental-ct-react';
import type { AgentId, AppSettings } from '~/api/generated';
import { MessagePaneApp, SwitcherApp } from './harness/AgentHarness';

/** The global assistant pick (v1.48): the sidebar switcher, and what a chat says after a switch. */

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

function settings(active: AgentId, codexInstalled = true): AppSettings {
  return {
    projects_root: '/Users/test/Projects',
    assistant_agent: active,
    agents: [
      { id: 'claude', label: 'Claude Code', installed: true, bin_path: '/usr/local/bin/claude' },
      {
        id: 'codex',
        label: 'Codex',
        installed: codexInstalled,
        bin_path: codexInstalled ? '/opt/homebrew/bin/codex' : null,
      },
    ],
  };
}

async function mockSettings(page: Page, initial: AppSettings) {
  let current = initial;
  const patches: unknown[] = [];
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    if (request.method() === 'OPTIONS') {
      await route.fulfill({ status: 204, headers: CORS, body: '' });
      return;
    }
    const url = new URL(request.url());
    if (url.pathname.endsWith('/settings') && request.method() === 'GET') {
      await json(route, 200, current);
      return;
    }
    if (url.pathname.endsWith('/settings') && request.method() === 'PATCH') {
      const body = JSON.parse(request.postData() ?? '{}');
      patches.push(body);
      current = { ...current, assistant_agent: body.assistant_agent };
      await json(route, 200, current);
      return;
    }
    if (url.pathname.endsWith('/chat/sessions/chat-1/messages') && request.method() === 'GET') {
      await json(route, 200, []);
      return;
    }
    await json(route, 404, { detail: `unexpected ${request.method()} ${url.pathname}` });
  });
  return { patches };
}

test('an agent whose CLI is missing is offered but cannot be picked', async ({ mount, page }) => {
  await mockSettings(page, settings('claude', false));
  await mount(<SwitcherApp />);

  const select = page.getByLabel('Assistant');
  await expect(select).toHaveValue('claude');
  await expect(select.getByRole('option', { name: 'Codex (not installed)' })).toBeDisabled();
  await expect(select.getByRole('option', { name: 'Claude Code' })).toBeEnabled();
});

test('an uninstalled agent that is still the pick is called out, not silently swapped', async ({
  mount,
  page,
}) => {
  await mockSettings(page, settings('codex', false));
  await mount(<SwitcherApp />);

  await expect(page.getByLabel('Assistant')).toHaveValue('codex');
  await expect(
    page.getByText("Codex isn't installed — AI features fail until it is."),
  ).toBeVisible();
});

test('picking an agent saves it, confirms it, and the select follows the saved value', async ({
  mount,
  page,
}) => {
  const { patches } = await mockSettings(page, settings('claude'));
  await mount(<SwitcherApp />);

  const select = page.getByLabel('Assistant');
  await expect(select).toHaveValue('claude');
  await select.selectOption('codex');

  await expect(page.getByText('Assistant switched to Codex')).toBeVisible();
  expect(patches).toEqual([{ assistant_agent: 'codex' }]);
  await expect(select).toHaveValue('codex');
});

test('a chat started on another agent says where the next message goes', async ({
  mount,
  page,
}) => {
  await mockSettings(page, settings('codex'));
  await mount(<MessagePaneApp sessionAgent="claude" />);

  await expect(
    page.getByText(
      'Started on Claude Code — your next message goes to Codex, with the conversation so far.',
    ),
  ).toBeVisible();
  await expect(page.getByRole('textbox', { name: 'Message' })).toHaveAttribute(
    'placeholder',
    /^Ask Codex to refine/,
  );
  await expect(page.getByText('Ask Codex about your installed skills and agents')).toBeVisible();
});

test('a chat already on the active agent shows no switch note', async ({ mount, page }) => {
  await mockSettings(page, settings('codex'));
  await mount(<MessagePaneApp sessionAgent="codex" />);

  await expect(page.getByRole('textbox', { name: 'Message' })).toHaveAttribute(
    'placeholder',
    /^Ask Codex to refine/,
  );
  await expect(page.getByText(/Started on/)).toHaveCount(0);
});

test('a chat with no reply yet shows no switch note', async ({ mount, page }) => {
  await mockSettings(page, settings('codex'));
  await mount(<MessagePaneApp sessionAgent={null} />);

  await expect(page.getByRole('textbox', { name: 'Message' })).toHaveAttribute(
    'placeholder',
    /^Ask Codex to refine/,
  );
  await expect(page.getByText(/Started on/)).toHaveCount(0);
});
