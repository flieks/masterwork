import { test, expect } from '@playwright/experimental-ct-react';
import type { Page } from '@playwright/test';
import type { CodingEvent } from '~/api/generated';
import { SessionRequest } from '~/features/sessions/components/SessionRequest';
import { firstRequest } from '~/features/sessions/events';
import { TestProviders } from './harness/TestProviders';
import { toolCall } from './harness/runFixtures';

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': '*',
  'Access-Control-Allow-Headers': '*',
};

function prompt(id: number, text: string): CodingEvent {
  return {
    ...toolCall(id, 1, '2026-08-12T10:00:00Z'),
    event_type: 'UserPromptSubmit',
    tool_name: null,
    payload: { prompt: text },
  };
}

const LONG = ['first line', 'second line', 'third line', 'fourth line'].join('\n');

/* ── which prompt is the request ──────────────────────────────────────────── */

test('the first prompt a person sent is the request', () => {
  const events = [prompt(1, 'give sessions a real title'), prompt(2, 'and now the card')];
  expect(firstRequest(events)?.text).toBe('give sessions a real title');
});

test('a run resumed by a background task still finds its human request', () => {
  const events = [
    prompt(1, '<task-notification><summary>agent finished</summary></task-notification>'),
    prompt(2, '<system-reminder>be brief</system-reminder>'),
    prompt(3, 'now do the thing'),
  ];
  expect(firstRequest(events)?.text).toBe('now do the thing');
});

test('a run with no human prompt has no request', () => {
  expect(firstRequest([])).toBeNull();
  expect(firstRequest([toolCall(1, 1, '2026-08-12T10:00:00Z')])).toBeNull();
});

/* ── the block ────────────────────────────────────────────────────────────── */

async function mockEvents(page: Page, events: CodingEvent[]): Promise<void> {
  await page.route('**/api/v1/**', async (route) => {
    if (route.request().method() === 'OPTIONS') {
      await route.fulfill({ status: 204, headers: CORS, body: '' });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: CORS,
      body: JSON.stringify(events),
    });
  });
}

/**
 * The whole request is always in the DOM and the clamp is what hides it — so
 * nothing here is lost to a copy-paste or a Ctrl-F, and the collapsed state is
 * a class rather than a substring. Whether three lines is what the clamp
 * actually shows is a CSS question, and this harness ships no Tailwind on
 * purpose (see `playwright-ct.config.ts`); `sessions.spec.ts` asks it instead.
 */
test('holds the whole request, collapsed behind the clamp', async ({ mount, page }) => {
  await mockEvents(page, [prompt(1, LONG)]);
  const block = await mount(
    <TestProviders>
      <SessionRequest sessionId="s1" />
    </TestProviders>,
  );
  const text = block.getByText('first line');
  await expect(text).toContainText('fourth line');
  await expect(text).toHaveClass(/line-clamp-3/);
});

test('renders nothing for a run that never carried a prompt', async ({ mount, page }) => {
  await mockEvents(page, [toolCall(1, 1, '2026-08-12T10:00:00Z')]);
  const block = await mount(
    <TestProviders>
      <SessionRequest sessionId="s1" />
    </TestProviders>,
  );
  await expect(block.getByText('Request')).toHaveCount(0);
});
