import { test, expect, type Page } from '@playwright/experimental-ct-react';
import type { CodingEvent } from '~/api/generated';
import { EventTimeline } from '~/features/sessions/components/EventTimeline';
import { SessionHeader } from '~/features/sessions/components/SessionHeader';
import { TestProviders } from './harness/TestProviders';
import { chatRun } from './harness/runFixtures';

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET,POST,PATCH,DELETE,OPTIONS',
  'Access-Control-Allow-Headers': '*',
};

function makeEvent(overrides: Partial<CodingEvent> = {}): CodingEvent {
  return {
    id: 1,
    session_id: 'sess-1',
    event_type: 'SessionStart',
    tool_name: null,
    payload: null,
    created_at: '2026-08-08T09:00:00Z',
    ...overrides,
  };
}

const HISTORY: CodingEvent[] = [
  makeEvent({ id: 1, event_type: 'SessionStart', payload: { cwd: '/tmp/demo' } }),
  makeEvent({
    id: 2,
    event_type: 'UserPromptSubmit',
    payload: { prompt: 'add a sessions screen' },
  }),
  makeEvent({
    id: 3,
    event_type: 'PostToolUse',
    tool_name: 'Bash',
    payload: { tool_input: { command: 'ls -la', description: 'List sessions feature files' } },
  }),
  makeEvent({
    id: 4,
    event_type: 'PostToolUse',
    tool_name: 'Read',
    payload: { tool_input: { file_path: '/Users/dev/Projects/masterwork/src/RunCard.tsx' } },
  }),
];

/**
 * Serves the event stream honouring the `after` cursor, so a poll only ever
 * receives what the component does not already hold.
 */
async function mockEvents(page: Page, all: CodingEvent[]): Promise<{ cursors: number[] }> {
  const cursors: number[] = [];

  await page.route('**/api/v1/**', async (route) => {
    const req = route.request();
    if (req.method() === 'OPTIONS') {
      await route.fulfill({ status: 204, headers: CORS, body: '' });
      return;
    }
    const after = Number(new URL(req.url()).searchParams.get('after') ?? 0);
    cursors.push(after);
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: CORS,
      body: JSON.stringify(all.filter((e) => e.id > after)),
    });
  });

  return { cursors };
}

test('renders the event stream as a timeline', async ({ mount, page }) => {
  await mockEvents(page, HISTORY);

  await mount(
    <TestProviders>
      <EventTimeline sessionId="sess-1" />
    </TestProviders>,
  );

  await expect(page.getByText('SessionStart')).toBeVisible();
  await expect(page.getByText('UserPromptSubmit')).toBeVisible();
  await expect(page.getByText('Bash')).toBeVisible();
  await expect(page.getByText('Read')).toBeVisible();
  // UserPromptSubmit renders its prompt inline.
  await expect(page.getByText('add a sessions screen')).toBeVisible();
});

test('a tool call says what it was called with, without being expanded', async ({
  mount,
  page,
}) => {
  await mockEvents(page, HISTORY);

  await mount(
    <TestProviders>
      <EventTimeline sessionId="sess-1" />
    </TestProviders>,
  );

  // The command's own description beats the command it describes.
  await expect(page.getByText('List sessions feature files')).toBeVisible();
  await expect(page.getByText('ls -la')).toHaveCount(0);

  // A read is named by its file; the whole path stays in the tooltip.
  await expect(page.getByText('RunCard.tsx')).toBeVisible();
  await expect(page.getByTitle('~/Projects/masterwork/src/RunCard.tsx')).toBeVisible();

  // Every tool event is a PostToolUse, so the chip only rides the rows where the
  // hook says something: the two non-tool events here, and nothing else.
  await expect(page.getByText('PostToolUse')).toHaveCount(0);
  await expect(page.getByText('SessionStart')).toBeVisible();
});

test('a run of one tool is one countable row, expandable to the calls', async ({ mount, page }) => {
  const reads = ['RunCard.tsx', 'timeline.ts', 'lanes.ts', 'RunWaterfall.tsx'].map((name, i) =>
    makeEvent({
      id: 10 + i,
      event_type: 'PostToolUse',
      tool_name: 'Read',
      payload: { tool_input: { file_path: `/Users/dev/masterwork/src/${name}` } },
    }),
  );
  await mockEvents(page, [
    ...HISTORY,
    ...reads,
    // Skills never fold: which skills a run loaded is the point of the screen.
    makeEvent({ id: 20, tool_name: 'Skill', payload: { tool_input: { skill: 'caveman' } } }),
    makeEvent({ id: 21, tool_name: 'Skill', payload: { tool_input: { skill: 'tdd' } } }),
  ]);

  await mount(
    <TestProviders>
      <EventTimeline sessionId="sess-1" />
    </TestProviders>,
  );

  const group = page.getByRole('button', { name: 'Read ×5' });
  await expect(group).toBeVisible();
  await expect(page.getByText('timeline.ts')).toHaveCount(0);

  await group.click();
  await expect(page.getByText('timeline.ts')).toBeVisible();
  await expect(page.getByText('lanes.ts')).toBeVisible();

  await expect(page.getByText('caveman')).toBeVisible();
  await expect(page.getByText('tdd')).toBeVisible();
  await expect(page.getByRole('button', { name: /Skill ×/ })).toHaveCount(0);
});

test('a prompt from a machine reads as a sentence, not as markup', async ({ mount, page }) => {
  await mockEvents(page, [
    makeEvent({
      id: 1,
      event_type: 'UserPromptSubmit',
      payload: {
        prompt: [
          '<task-notification>',
          '<task-id>ad383abed70243167</task-id>',
          '<status>completed</status>',
          '<summary>Agent "Build factory pipeline runner" finished</summary>',
          '<result>Built and verified.</result>',
          '</task-notification>',
        ].join('\n'),
      },
    }),
    makeEvent({
      id: 2,
      event_type: 'UserPromptSubmit',
      payload: { prompt: '<task-notification>\n<status>failed</status>\n</task-notification>' },
    }),
  ]);

  await mount(
    <TestProviders>
      <EventTimeline sessionId="sess-1" />
    </TestProviders>,
  );

  await expect(page.getByText('Agent "Build factory pipeline runner" finished')).toBeVisible();
  await expect(page.getByText('Background task failed')).toBeVisible();
  await expect(page.getByText('<task-id>')).toHaveCount(0);

  // The markup is still one click away, in the payload the row already had.
  await page.getByRole('button', { name: 'UserPromptSubmit' }).first().click();
  await expect(page.getByText(/task-notification/)).toBeVisible();
});

test('tool badges are coloured by what the tool does', async ({ mount, page }) => {
  await mockEvents(page, [
    ...HISTORY,
    makeEvent({ id: 10, tool_name: 'Write', payload: { tool_input: { file_path: '/a/b.ts' } } }),
    makeEvent({ id: 11, tool_name: 'Skill', payload: { tool_input: { skill: 'tdd' } } }),
    makeEvent({
      id: 12,
      tool_name: 'mcp__Claude_Browser__navigate',
      payload: { tool_input: { url: 'http://localhost:5192' } },
    }),
    makeEvent({ id: 13, tool_name: 'SomeToolShippedTomorrow', payload: { tool_input: {} } }),
  ]);

  await mount(
    <TestProviders>
      <EventTimeline sessionId="sess-1" />
    </TestProviders>,
  );

  // Grouped by act: looking, changing, running, and skills loudest of all.
  await expect(page.getByTitle('Read')).toHaveClass(/sky/);
  await expect(page.getByTitle('Write')).toHaveClass(/amber/);
  await expect(page.getByTitle('Bash')).toHaveClass(/violet/);
  await expect(page.getByTitle('Skill')).toHaveClass(/fuchsia/);
  // Whatever the server, an MCP tool lands in one band.
  await expect(page.getByTitle('mcp__Claude_Browser__navigate')).toHaveClass(/cyan/);
  // A tool nobody has mapped yet renders neutral rather than crashing.
  await expect(page.getByTitle('SomeToolShippedTomorrow')).toHaveClass(/bg-muted/);
});

test('payload is collapsed until the row is clicked, then pretty-printed', async ({
  mount,
  page,
}) => {
  await mockEvents(page, HISTORY);

  await mount(
    <TestProviders>
      <EventTimeline sessionId="sess-1" />
    </TestProviders>,
  );

  // The header itself is the toggle — there is no separate "Payload" row.
  await expect(page.getByRole('button', { name: 'Payload' })).toHaveCount(0);

  const toggle = page.getByRole('button', { name: 'SessionStart' });
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await expect(page.getByText('"cwd": "/tmp/demo"')).toHaveCount(0);

  await toggle.click();

  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  await expect(page.getByText('"cwd": "/tmp/demo"')).toBeVisible();
});

test('appends new events on the next poll using the id cursor', async ({ mount, page }) => {
  const stream = [...HISTORY];
  const { cursors } = await mockEvents(page, stream);

  await mount(
    <TestProviders>
      <EventTimeline sessionId="sess-1" live />
    </TestProviders>,
  );

  await expect(page.getByText('List sessions feature files')).toBeVisible();
  expect(cursors[0]).toBe(0);

  // Arrives between two polls, as a live session's events do.
  stream.push(makeEvent({ id: 5, event_type: 'SessionEnd', created_at: '2026-08-08T09:20:00Z' }));

  await expect(page.getByText('SessionEnd')).toBeVisible();
  // History is not refetched: the poll asked only for events after the last id held.
  expect(cursors.at(-1)).toBe(4);
  await expect(page.getByText('SessionStart')).toHaveCount(1);
});

test('empty timeline explains a live session has not fired a hook yet', async ({ mount, page }) => {
  await mockEvents(page, []);

  await mount(
    <TestProviders>
      <EventTimeline sessionId="sess-1" live />
    </TestProviders>,
  );

  await expect(page.getByText('No events yet')).toBeVisible();
  await expect(page.getByText(/has not fired a hook yet/)).toBeVisible();
});

test('the header leads with the request and its telemetry, stats stay collapsed', async ({
  mount,
  page,
}) => {
  const session = chatRun({
    title: 'add a sessions screen',
    ended_at: '2026-08-08T09:20:00Z',
    status: 'success',
    cost_usd: 0.4213,
    tokens_total: 899_924,
    tokens_out: 8306,
    duration_seconds: 1200,
    stats: { turns: 12, total_cost_usd: 0.4213, some_future_key: { nested: true } },
  });

  await mount(
    <TestProviders>
      <SessionHeader session={session} />
    </TestProviders>,
  );

  await expect(page.getByRole('heading', { name: 'add a sessions screen' })).toBeVisible();
  await expect(page.getByText('success')).toBeVisible();
  await expect(page.getByText('/Users/dev/Projects/masterwork')).toBeVisible();
  await expect(page.getByTitle('Cost')).toContainText('$0.42');
  await expect(page.getByText('2m 31s active')).toBeVisible();
  await expect(page.getByTitle('Total tokens')).toContainText('899.9k');
  // Nothing reported a cache-read count for this session.
  await expect(page.getByTitle('Cache-read tokens')).toContainText('—');

  // Cost has a column of its own now, so it is not repeated as a raw stat.
  await expect(page.getByText('Turns')).toBeVisible();
  await expect(page.getByText('12')).toBeVisible();
  // Unknown keys get no tile, but survive in the raw JSON.
  await expect(page.getByText('some_future_key')).toHaveCount(0);
  await page.getByRole('button', { name: 'Raw stats' }).click();
  await expect(page.getByText(/some_future_key/)).toBeVisible();
});
