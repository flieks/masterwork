import { test, expect, type Page, type Route } from '@playwright/experimental-ct-react';
import { InterviewQuestions } from '~/features/sessions/components/InterviewQuestions';
import { Toaster } from '~/components/ui/sonner';
import { TestProviders } from './harness/TestProviders';

// The generated client targets a cross-origin backend (localhost:8008), so every
// fulfilled response needs CORS headers and OPTIONS preflights must be answered.
const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET,POST,PATCH,DELETE,OPTIONS',
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

const WAITING_LAUNCH = {
  id: 1,
  project_path: '/Users/test/Projects/alpha',
  request_text: 'Add a subtract function',
  mode: 'interview',
  launched_at: new Date(0).toISOString(),
  pid: 4242,
  run_id: 'a1b2c3d4',
  launched: true,
  interview: {
    launch_id: 1,
    run_id: 'a1b2c3d4',
    state: 'waiting',
    run_state: 'waiting_input',
    questions: [
      { id: 'q1', question: 'Use SQLite for now?' },
      { id: 'q2', question: 'Skip auth on this endpoint?' },
    ],
  },
};

/** Serves `launches` from a mutable array so a test can flip a launch's state
 * (e.g. after a successful submit) and records every answers POST body. */
function mockLauncher(page: Page, launches: unknown[]) {
  const submitCalls: { launchId: string; body: unknown }[] = [];
  let submitStatus = 200;

  return page
    .route('**/api/v1/**', async (route) => {
      const request = route.request();
      if (request.method() === 'OPTIONS') {
        await route.fulfill({ status: 204, headers: CORS, body: '' });
        return;
      }
      const url = request.url();

      if (url.endsWith('/launcher/launches') && request.method() === 'GET') {
        await json(route, 200, launches);
        return;
      }
      const answersMatch = url.match(/\/launches\/(\d+)\/answers$/);
      if (answersMatch && request.method() === 'POST') {
        const body = JSON.parse(request.postData() ?? '{}');
        submitCalls.push({ launchId: answersMatch[1], body });
        if (submitStatus !== 200) {
          await json(route, submitStatus, { detail: 'not waiting for answers' });
          return;
        }
        await json(route, 200, {
          launch_id: Number(answersMatch[1]),
          run_id: 'a1b2c3d4',
          resumed: true,
          pid: 5000,
        });
        return;
      }

      throw new Error(`unexpected request in interviewQuestions test: ${request.method()} ${url}`);
    })
    .then(() => ({
      submitCalls,
      failNextSubmit: () => {
        submitStatus = 409;
      },
    }));
}

test('a waiting launch renders one required field per question, disabled until all are filled', async ({
  mount,
  page,
}) => {
  await mockLauncher(page, [WAITING_LAUNCH]);

  await mount(
    <TestProviders>
      <InterviewQuestions />
    </TestProviders>,
  );

  const submit = page.getByRole('button', { name: 'Answer and continue' });
  await expect(page.getByLabel('Use SQLite for now?')).toBeVisible();
  await expect(page.getByLabel('Skip auth on this endpoint?')).toBeVisible();
  await expect(submit).toBeDisabled();

  await page.getByLabel('Use SQLite for now?').fill('Yes, SQLite is fine');
  await expect(submit).toBeDisabled(); // second question still blank

  await page.getByLabel('Skip auth on this endpoint?').fill('No, require a token');
  await expect(submit).toBeEnabled();
});

test('submitting posts the answers in question order and confirms the run resumed', async ({
  mount,
  page,
}) => {
  const { submitCalls } = await mockLauncher(page, [WAITING_LAUNCH]);

  await mount(
    <TestProviders>
      <InterviewQuestions />
      <Toaster />
    </TestProviders>,
  );

  await page.getByLabel('Use SQLite for now?').fill('Yes, SQLite is fine');
  await page.getByLabel('Skip auth on this endpoint?').fill('No, require a token');
  await page.getByRole('button', { name: 'Answer and continue' }).click();

  await expect.poll(() => submitCalls.length).toBe(1);
  expect(submitCalls[0]).toMatchObject({
    launchId: '1',
    body: {
      answers: [
        { id: 'q1', answer: 'Yes, SQLite is fine' },
        { id: 'q2', answer: 'No, require a token' },
      ],
    },
  });
  await expect(page.getByText('Run resumed')).toBeVisible();
});

test('a launch that is not waiting renders nothing', async ({ mount, page }) => {
  const notWaiting = {
    ...WAITING_LAUNCH,
    interview: { ...WAITING_LAUNCH.interview, state: 'running', questions: [] },
  };
  await mockLauncher(page, [notWaiting]);

  await mount(
    <TestProviders>
      <InterviewQuestions />
    </TestProviders>,
  );

  await expect(page.getByRole('button', { name: 'Answer and continue' })).toHaveCount(0);
});

test('a 409 surfaces an error toast and leaves the form in place', async ({ mount, page }) => {
  const { failNextSubmit } = await mockLauncher(page, [WAITING_LAUNCH]);
  failNextSubmit();

  await mount(
    <TestProviders>
      <InterviewQuestions />
      <Toaster />
    </TestProviders>,
  );

  await page.getByLabel('Use SQLite for now?').fill('Yes');
  await page.getByLabel('Skip auth on this endpoint?').fill('No');
  await page.getByRole('button', { name: 'Answer and continue' }).click();

  await expect(page.getByText('Could not submit answers')).toBeVisible();
  await expect(page.getByLabel('Use SQLite for now?')).toHaveValue('Yes');
});
