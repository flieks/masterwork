import { test, expect, type Page } from '@playwright/experimental-ct-react';
import type { Project, Simulation } from '~/api/generated';
import { ProjectSimulationTab } from '~/features/projects/components/ProjectSimulationTab';
import { TestProviders } from './harness/TestProviders';

function makeProject(overrides: Partial<Project> = {}): Project {
  return {
    id: 'proj-1',
    name: 'Deploy pipeline',
    goal: 'Ship the app to Azure.',
    scenario: '',
    flow_mermaid: null,
    // Non-empty: the run buttons are disabled (with a banner) when nothing is linked.
    asset_ids: ['claude:skill:frontend-dev'],
    created_at: '2026-07-18T10:00:00Z',
    updated_at: '2026-07-18T10:00:00Z',
    ...overrides,
  };
}

function makeSimulation(overrides: Partial<Simulation> = {}): Simulation {
  return {
    id: 'sim-1',
    project_id: 'proj-1',
    status: 'running',
    scenario: '',
    score: null,
    verdict: null,
    summary: null,
    analysis: null,
    trace_mermaid: null,
    suggestions: [],
    error: null,
    created_at: '2026-07-18T11:00:00Z',
    completed_at: null,
    ...overrides,
  };
}

// The generated client targets a cross-origin backend (localhost:8008), so every
// fulfilled response needs CORS headers and OPTIONS preflights must be answered.
const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET,POST,PATCH,DELETE,OPTIONS',
  'Access-Control-Allow-Headers': '*',
};

type RouteOptions = { generatedScenario?: string; delayMs?: number; simulations?: Simulation[] };

async function mockApi(page: Page, opts: RouteOptions = {}): Promise<void> {
  const {
    generatedScenario = 'Auto-drafted: a user asks to add auth and deploy.',
    delayMs = 0,
    simulations = [],
  } = opts;

  await page.route('**/api/v1/**', async (route) => {
    const req = route.request();
    if (req.method() === 'OPTIONS') {
      await route.fulfill({ status: 204, headers: CORS, body: '' });
      return;
    }
    const url = req.url();
    const json = (status: number, data: unknown) =>
      route.fulfill({
        status,
        contentType: 'application/json',
        headers: CORS,
        body: JSON.stringify(data),
      });

    if (delayMs > 0) await new Promise((r) => setTimeout(r, delayMs));

    if (url.includes('/simulations/scenario') && req.method() === 'POST') {
      await json(200, { scenario: generatedScenario });
      return;
    }
    if (url.endsWith('/simulations') && req.method() === 'POST') {
      await json(202, makeSimulation());
      return;
    }
    if (url.endsWith('/simulations') && req.method() === 'GET') {
      await json(200, simulations);
      return;
    }
    // Project refetch after invalidation, or anything else.
    await json(200, makeProject());
  });
}

test('textarea is prefilled from project.scenario', async ({ mount, page }) => {
  await mockApi(page);

  await mount(
    <TestProviders>
      <ProjectSimulationTab project={makeProject({ scenario: 'Deploy a dashboard with auth' })} />
    </TestProviders>,
  );

  await expect(page.getByLabel('Scenario to simulate')).toHaveValue('Deploy a dashboard with auth');
});

test('Generate scenario fills the textarea with the returned scenario', async ({ mount, page }) => {
  const generated = 'A user asks for a new billing page, then deploys to production.';
  await mockApi(page, { generatedScenario: generated, delayMs: 300 });

  await mount(
    <TestProviders>
      <ProjectSimulationTab project={makeProject({ scenario: '' })} />
    </TestProviders>,
  );

  const textarea = page.getByLabel('Scenario to simulate');
  await expect(textarea).toHaveValue('');

  await page.getByRole('button', { name: 'Generate scenario' }).click();

  // While pending: busy label, and both buttons + textarea disabled.
  await expect(page.getByRole('button', { name: 'Generating…' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Run simulation' })).toBeDisabled();
  await expect(textarea).toBeDisabled();

  // On success: the returned scenario fills the textarea.
  await expect(textarea).toHaveValue(generated);
  await expect(page.getByRole('button', { name: 'Generate scenario' })).toBeEnabled();
});

test('no linked assets disables runs and points to Overview', async ({ mount, page }) => {
  await mockApi(page);

  await mount(
    <TestProviders>
      <ProjectSimulationTab project={makeProject({ asset_ids: [] })} />
    </TestProviders>,
  );

  await expect(page.getByText('No assets linked')).toBeVisible();
  await expect(page.getByRole('link', { name: 'Link assets in Overview' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Run simulation' })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Autopilot' })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Generate scenario' })).toBeDisabled();
});

test('running a simulation does not clear the textarea', async ({ mount, page }) => {
  await mockApi(page, { delayMs: 300 });

  await mount(
    <TestProviders>
      <ProjectSimulationTab project={makeProject({ scenario: 'Keep me after the run' })} />
    </TestProviders>,
  );

  const textarea = page.getByLabel('Scenario to simulate');
  await expect(textarea).toHaveValue('Keep me after the run');

  await page.getByRole('button', { name: 'Run simulation' }).click();

  // In flight (the old behaviour cleared here) — value survives.
  await expect(page.getByRole('button', { name: 'Starting…' })).toBeVisible();
  await expect(textarea).toHaveValue('Keep me after the run');

  // After the run started successfully — value still survives for reuse.
  await expect(page.getByRole('button', { name: 'Run simulation' })).toBeEnabled();
  await expect(textarea).toHaveValue('Keep me after the run');
});

test('autopilot scenario rotation is mirrored into the textarea', async ({ mount, page }) => {
  const rotated = 'A fresh scenario the autopilot generated after scoring 100.';
  await mockApi(page, {
    simulations: [
      makeSimulation({
        status: 'running',
        scenario: rotated,
        autopilot_run_id: 'run-1',
        autopilot_iteration: 2,
        autopilot_total: 5,
      }),
    ],
  });

  await mount(
    <TestProviders>
      <ProjectSimulationTab project={makeProject({ scenario: 'The original scenario' })} />
    </TestProviders>,
  );

  await expect(page.getByLabel('Scenario to simulate')).toHaveValue(rotated);
  await expect(page.getByText('Autopilot run 2/5')).toBeVisible();
});
