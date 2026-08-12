import { test, expect } from '@playwright/experimental-ct-react';
import type { Page } from '@playwright/test';
import type { CodingEvent } from '~/api/generated';
import { RouteDecisionNote } from '~/features/sessions/components/RouteDecisionNote';
import { routeDecision } from '~/features/sessions/runs';
import { TestProviders } from './harness/TestProviders';
import { toolCall } from './harness/runFixtures';

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': '*',
  'Access-Control-Allow-Headers': '*',
};

function bashEvent(id: number, command: string): CodingEvent {
  return {
    ...toolCall(id, 1, '2026-08-12T10:00:00Z'),
    tool_name: 'Bash',
    payload: { tool_input: { command } },
  };
}

/* ── the parser ───────────────────────────────────────────────────────────── */

test('finds the router marker and strips the echo quoting', () => {
  const events = [
    toolCall(1, 1, '2026-08-12T10:00:00Z'),
    bashEvent(2, 'echo "masterwork:route=chat -- taste calls and Figma back-and-forth"'),
  ];
  expect(routeDecision(events)).toEqual({
    verdict: 'chat',
    reason: 'taste calls and Figma back-and-forth',
  });
});

test('the latest verdict wins when a session routes twice', () => {
  const events = [
    bashEvent(1, 'echo "masterwork:route=chat -- exploring first"'),
    bashEvent(2, 'echo "masterwork:route=factory -- spec pinned, checks exist"'),
  ];
  expect(routeDecision(events)).toEqual({
    verdict: 'factory',
    reason: 'spec pinned, checks exist',
  });
});

test('a bare marker without a reason still parses', () => {
  expect(routeDecision([bashEvent(1, 'echo "masterwork:route=chat"')])).toEqual({
    verdict: 'chat',
    reason: null,
  });
});

test('sessions that never routed return null', () => {
  expect(routeDecision([])).toBeNull();
  expect(routeDecision([toolCall(1, 1, '2026-08-12T10:00:00Z')])).toBeNull();
  expect(routeDecision([bashEvent(1, 'echo "masterwork:route=maybe -- nope"')])).toBeNull();
});

/* ── the note ─────────────────────────────────────────────────────────────── */

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

test('renders the verdict and reason in one line', async ({ mount, page }) => {
  await mockEvents(page, [
    bashEvent(1, 'echo "masterwork:route=chat -- taste calls, no pinned spec"'),
  ]);
  const note = await mount(
    <TestProviders>
      <RouteDecisionNote sessionId="s1" />
    </TestProviders>,
  );
  await expect(note.getByText('Routed to chat')).toBeVisible();
  await expect(note.getByText('— taste calls, no pinned spec')).toBeVisible();
});

test('renders nothing for a session that never routed', async ({ mount, page }) => {
  await mockEvents(page, [toolCall(1, 1, '2026-08-12T10:00:00Z')]);
  const note = await mount(
    <TestProviders>
      <RouteDecisionNote sessionId="s1" />
    </TestProviders>,
  );
  await expect(note.getByText('Routed to')).toHaveCount(0);
});
