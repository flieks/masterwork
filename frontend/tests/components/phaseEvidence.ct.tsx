import { test, expect, type Page } from '@playwright/experimental-ct-react';
import { Route, Routes } from 'react-router-dom';
import type { CodingSessionDetail } from '~/api/generated';
import { SessionDetailPage } from '~/features/sessions/components/SessionDetailPage';
import { TestProviders } from './harness/TestProviders';
import {
  DOCUMENT_GATE_CHECKS,
  FAILED_PARSE_ATTEMPTS,
  envelopeAttempt,
  factoryRun,
  gateCheck,
} from './harness/runFixtures';

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET,POST,PATCH,DELETE,OPTIONS',
  'Access-Control-Allow-Headers': '*',
};

/** `phase(seq)` ids in the fixture run: plan 5 … document 9. */
const PLAN_ID = 5;
const DOCUMENT_ID = 9;

async function mockRun(page: Page, run: CodingSessionDetail): Promise<void> {
  await page.route('**/api/v1/**', async (route) => {
    if (route.request().method() === 'OPTIONS') {
      await route.fulfill({ status: 204, headers: CORS, body: '' });
      return;
    }
    const body = new URL(route.request().url()).pathname.endsWith('/events') ? [] : run;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: CORS,
      body: JSON.stringify(body),
    });
  });
}

function mountDetail(mount: Parameters<Parameters<typeof test>[1]>[0]['mount']) {
  return mount(
    <TestProviders initialEntries={['/sessions/factory-3f5a20b0']}>
      <Routes>
        <Route path="/sessions/:id" element={<SessionDetailPage />} />
      </Routes>
    </TestProviders>,
  );
}

/** The `document` stage of `factory-638e7eb0`: fails on 1, passes on 2. */
function documentRun(): CodingSessionDetail {
  const run = factoryRun({
    gate_checks: DOCUMENT_GATE_CHECKS.map((check) => ({ ...check, phase_id: DOCUMENT_ID })),
    envelopes: [
      envelopeAttempt(20, 1, { phase_id: DOCUMENT_ID, role: 'document' }),
      envelopeAttempt(21, 2, { phase_id: DOCUMENT_ID, role: 'document' }),
    ],
  });
  return {
    ...run,
    phases: run.phases.map((phase) =>
      phase.id === DOCUMENT_ID ? { ...phase, gates_passed: 7, gates_failed: 1 } : phase,
    ),
  };
}

test('a failed parse shows the error and the reply it was read out of', async ({ mount, page }) => {
  await mockRun(
    page,
    factoryRun({
      envelopes: FAILED_PARSE_ATTEMPTS,
      gate_checks: [
        gateCheck(1, 'envelope', false, 'no fenced code block found in the reply'),
        gateCheck(2, 'envelope', false, 'missing required field(s) for the plan role', {
          attempt: 2,
        }),
      ],
    }),
  );
  await mountDetail(mount);
  await page.getByRole('button', { name: 'Phase plan' }).click();

  const attempts = page.getByLabel('Phase detail').getByLabel('Envelope attempts');
  await expect(attempts.getByText('did not parse')).toHaveCount(3);

  // Each attempt's own error, verbatim — three distinct reasons, not one count.
  await expect(attempts.getByText('no fenced code block found in the reply')).toBeVisible();
  await expect(
    attempts.getByText('missing required field(s) for the plan role: status, artifacts'),
  ).toBeVisible();
  await expect(attempts.getByText(/"status" must be one of ok, blocked, failed/)).toBeVisible();

  // The raw reply is the payload — a failed parse opens it without a click.
  await expect(attempts.getByText(/Done\. Plan updated/)).toHaveCount(3);
  await expect(attempts.getByText('Attempt 3')).toBeVisible();
});

test('a parsed attempt keeps its body and reply folded away', async ({ mount, page }) => {
  await mockRun(page, documentRun());
  await mountDetail(mount);
  await page.getByRole('button', { name: 'Phase document' }).click();

  const attempts = page.getByLabel('Phase detail').getByLabel('Envelope attempts');
  await expect(attempts.getByText('parsed')).toHaveCount(2);
  await expect(attempts.getByText('"status": "ok"')).toHaveCount(0);

  await attempts.getByRole('button', { name: 'Envelope body' }).first().click();
  await expect(attempts.getByText(/"artifacts": \[/)).toBeVisible();
});

test('a failing gate check reads its note verbatim, next to the attempt it failed on', async ({
  mount,
  page,
}) => {
  await mockRun(page, documentRun());
  await mountDetail(mount);
  await page.getByRole('button', { name: 'Phase document' }).click();

  const panel = page.getByLabel('Phase detail');
  // The counts stay as the summary line…
  await expect(panel.getByText('7 gates passed')).toBeVisible();
  // …and the note is what the reader acts on.
  const gates = panel.getByLabel('Gate checks');
  await expect(
    gates.getByText('claimed but not changed on disk: CHANGELOG.md, greet.py, test_greet.py'),
  ).toBeVisible();
  await expect(gates.getByText('changed_files')).toBeVisible();

  // The correction round is legible: attempt 1 failed, attempt 2 did not.
  await expect(gates.getByText('Attempt 1')).toBeVisible();
  await expect(gates.getByText('1 failed')).toBeVisible();
  await expect(gates.getByText('Attempt 2')).toBeVisible();
  await expect(gates.getByText('all passed')).toBeVisible();
});

test('passing checks fold into a count so the failure leads', async ({ mount, page }) => {
  await mockRun(page, documentRun());
  await mountDetail(mount);
  await page.getByRole('button', { name: 'Phase document' }).click();

  const gates = page.getByLabel('Phase detail').getByLabel('Gate checks');
  // Seven passes exist, and none of them is competing with the one failure.
  await expect(gates.getByText('parsed a valid document envelope')).toHaveCount(0);
  await expect(gates.getByText('1 artifact(s) present')).toHaveCount(0);
  await expect(gates.getByText(/claimed but not changed on disk/)).toBeVisible();
  await expect(gates.getByRole('button', { name: '3 passed' })).toBeVisible();
  await expect(gates.getByRole('button', { name: '4 passed' })).toBeVisible();

  // They are folded, not dropped — "which gates never fail" is one click away.
  await gates.getByRole('button', { name: '3 passed' }).click();
  await expect(gates.getByText('parsed a valid document envelope')).toBeVisible();
  await expect(gates.getByText('1 artifact(s) present')).toBeVisible();
});

test('a check that names an item shows the thing it checked', async ({ mount, page }) => {
  await mockRun(
    page,
    factoryRun({
      gate_checks: [
        gateCheck(60, 'checks', true, 'exited 0', {
          phase_id: PLAN_ID,
          item: 'python3 -m unittest discover -q',
        }),
      ],
    }),
  );
  await mountDetail(mount);
  await page.getByRole('button', { name: 'Phase plan' }).click();

  const gates = page.getByLabel('Phase detail').getByLabel('Gate checks');
  await gates.getByRole('button', { name: '1 passed' }).click();
  await expect(gates.getByText('checks · python3 -m unittest discover -q')).toBeVisible();
});

test('a recovered attempt with no body says so instead of showing an empty box', async ({
  mount,
  page,
}) => {
  await mockRun(
    page,
    factoryRun({
      envelopes: [
        envelopeAttempt(8, 1, {
          origin: 'recovered',
          status: null,
          body: null,
          raw_text: null,
        }),
      ],
      gate_checks: [
        gateCheck(70, 'envelope', true, 'parsed a valid plan envelope', { origin: 'recovered' }),
      ],
    }),
  );
  await mountDetail(mount);
  await page.getByRole('button', { name: 'Phase plan' }).click();

  const panel = page.getByLabel('Phase detail');
  await expect(panel.getByLabel('Envelope attempts').getByText('recovered')).toBeVisible();
  await expect(
    panel.getByText(/rebuilt from the event stream, which never carried them/),
  ).toBeVisible();
  // No disclosure promising content that was never recorded.
  await expect(panel.getByRole('button', { name: 'Envelope body' })).toHaveCount(0);
  await expect(panel.getByRole('button', { name: /Raw reply/ })).toHaveCount(0);
});

test('evidence that names no phase is shown at run level rather than dropped', async ({
  mount,
  page,
}) => {
  await mockRun(
    page,
    factoryRun({
      gate_checks: [
        gateCheck(80, 'boundary', false, 'wrote outside the boundary before any stage started', {
          phase_id: null,
        }),
        gateCheck(81, 'envelope', true, 'parsed a valid plan envelope', { phase_id: PLAN_ID }),
      ],
      envelopes: [envelopeAttempt(90, 1, { phase_id: null, role: null })],
    }),
  );
  await mountDetail(mount);

  // Visible with no phase selected — it belongs to no phase, so nothing to click.
  const orphans = page.getByLabel('Unattributed evidence');
  await expect(orphans).toBeVisible();
  await expect(
    orphans.getByText('wrote outside the boundary before any stage started'),
  ).toBeVisible();
  await expect(orphans.getByLabel('Envelope attempts')).toBeVisible();

  // And it does not leak into a stage that never claimed it.
  await page.getByRole('button', { name: 'Phase plan' }).click();
  const gates = page.getByLabel('Phase detail').getByLabel('Gate checks');
  await expect(gates.getByText('parsed a valid plan envelope')).toHaveCount(0);
  await gates.getByRole('button', { name: '1 passed' }).click();
  await expect(gates.getByText('parsed a valid plan envelope')).toBeVisible();
  await expect(gates.getByText(/wrote outside the boundary/)).toHaveCount(0);
});

test('a run with no evidence grows no empty sections', async ({ mount, page }) => {
  await mockRun(page, factoryRun());
  await mountDetail(mount);
  await page.getByRole('button', { name: 'Phase plan' }).click();

  await expect(page.getByLabel('Phase detail')).toBeVisible();
  await expect(page.getByLabel('Gate checks')).toHaveCount(0);
  await expect(page.getByLabel('Envelope attempts')).toHaveCount(0);
  await expect(page.getByLabel('Unattributed evidence')).toHaveCount(0);
});
