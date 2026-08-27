import { test, expect, type Page, type Route } from '@playwright/experimental-ct-react';
import type { CatalogSearchResponse, CatalogSkillDetail, InstalledSkill } from '~/api/generated';
import { Toaster } from '~/components/ui/sonner';
import { AssetListPage } from '~/features/assets/components/AssetListPage';
import { TestProviders } from './harness/TestProviders';
import {
  catalogSearchResponse,
  catalogSkill,
  catalogSkillDetail,
  installedCatalogSkillDetail,
  installedSkill,
  unlicensedCatalogSkillDetail,
} from './harness/catalogFixtures';

// The generated client targets a cross-origin backend (localhost:8008), so every
// fulfilled response needs CORS headers and OPTIONS preflights must be answered.
const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET,POST,PATCH,DELETE,OPTIONS',
  'Access-Control-Allow-Headers': '*',
};

function json(route: Route, body: unknown) {
  return route.fulfill({
    status: 200,
    contentType: 'application/json',
    headers: CORS,
    body: JSON.stringify(body),
  });
}

interface CatalogRoutes {
  /** Every non-preflight request, as "METHOD path". */
  calls: string[];
}

async function mockCatalog(
  page: Page,
  initial: {
    search?: CatalogSearchResponse;
    detail?: CatalogSkillDetail;
    installed?: InstalledSkill;
  } = {},
): Promise<CatalogRoutes> {
  const search = initial.search ?? catalogSearchResponse();
  const detail = initial.detail ?? catalogSkillDetail();
  const installed = initial.installed ?? installedSkill();
  const calls: string[] = [];

  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    if (request.method() === 'OPTIONS') {
      await route.fulfill({ status: 204, headers: CORS, body: '' });
      return;
    }
    const path = new URL(request.url()).pathname;
    calls.push(`${request.method()} ${path}`);

    if (path === '/api/v1/skills/catalog') {
      await json(route, search);
    } else if (path.startsWith('/api/v1/skills/catalog/')) {
      await json(route, detail);
    } else if (path === '/api/v1/skills/install') {
      await json(route, installed);
    } else if (path === '/api/v1/assets') {
      await json(route, []);
    } else {
      await route.fulfill({ status: 404, headers: CORS, body: '{}' });
    }
  });

  return { calls };
}

async function mountCatalogTab(
  mount: Parameters<Parameters<typeof test>[1]>[0]['mount'],
  page: Page,
  query = 'dev',
) {
  const component = await mount(
    <TestProviders initialEntries={['/skills?view=catalog']}>
      <AssetListPage kind="skill" />
      <Toaster />
    </TestProviders>,
  );
  // An empty query renders the prompt state and fires no request by design, so
  // every test types one before there is anything to assert on.
  await page.getByLabel('Search the skill catalog').fill(query);
  return component;
}

test('the catalog tab is selected from the URL and lists both results', async ({ mount, page }) => {
  await mockCatalog(page);
  await mountCatalogTab(mount, page);

  await expect(page.getByRole('tab', { name: 'Catalog' })).toHaveAttribute('aria-selected', 'true');

  const licensed = page.getByRole('button', { name: /Frontend Dev/ });
  await expect(licensed).toContainText('acme/frontend-dev');
  await expect(licensed).toContainText('42 installs');
  await expect(licensed).toContainText('MIT');

  const unlicensed = page.getByRole('button', { name: /Beta Tools/ });
  await expect(unlicensed).toContainText('other/beta-tools');
  await expect(unlicensed).toContainText('No license — all rights reserved');
  await expect(unlicensed).not.toContainText('installs');
});

test('opening the preview shows the SKILL.md as plain text', async ({ mount, page }) => {
  await mockCatalog(page);
  await mountCatalogTab(mount, page);

  await page.getByRole('button', { name: /Frontend Dev/ }).click();

  const dialog = page.getByRole('dialog');
  await expect(dialog).toContainText('Use React and Vite.');
  await expect(dialog.getByRole('button', { name: 'Install' })).toBeVisible();
});

test('installing a licensed skill posts once', async ({ mount, page }) => {
  const routes = await mockCatalog(page);
  await mountCatalogTab(mount, page);

  await page.getByRole('button', { name: /Frontend Dev/ }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Install' }).click();

  await expect(page.getByText('Installed Frontend Dev')).toBeVisible();
  expect(routes.calls.filter((c) => c === 'POST /api/v1/skills/install')).toHaveLength(1);
});

test('installing an unlicensed skill needs a second, risk-naming click', async ({ mount, page }) => {
  const routes = await mockCatalog(page, { detail: unlicensedCatalogSkillDetail() });
  await mountCatalogTab(mount, page);

  await page.getByRole('button', { name: /Beta Tools/ }).click();

  const dialog = page.getByRole('dialog');
  await expect(dialog).toContainText('No license — all rights reserved');
  await dialog.getByRole('button', { name: 'Install' }).click();

  // The first click only swaps the label — nothing has posted yet.
  expect(routes.calls.filter((c) => c === 'POST /api/v1/skills/install')).toHaveLength(0);
  const riskButton = dialog.getByRole('button', {
    name: 'Install anyway — no license grants you the right to use this',
  });
  await expect(riskButton).toBeVisible();

  await riskButton.click();

  await expect(page.getByText('Installed Beta Tools')).toBeVisible();
  expect(routes.calls.filter((c) => c === 'POST /api/v1/skills/install')).toHaveLength(1);
});

test('a skill already on disk offers a guarded reinstall, not a failing install', async ({
  mount,
  page,
}) => {
  const routes = await mockCatalog(page, {
    search: catalogSearchResponse({ skills: [catalogSkill({ installed: true })] }),
    detail: installedCatalogSkillDetail(),
  });
  await mountCatalogTab(mount, page);

  // The list says so before the dialog is even opened.
  await expect(page.getByRole('button', { name: /Frontend Dev/ })).toContainText('Installed');

  await page.getByRole('button', { name: /Frontend Dev/ }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toContainText('Installed');

  // The primary action is a reinstall, and the first click only arms it.
  await dialog.getByRole('button', { name: 'Reinstall' }).click();
  expect(routes.calls.filter((c) => c === 'POST /api/v1/skills/install')).toHaveLength(0);

  await dialog.getByRole('button', { name: 'Replace the copy already on disk' }).click();
  await expect(page.getByText('Installed Frontend Dev')).toBeVisible();
  expect(routes.calls.filter((c) => c === 'POST /api/v1/skills/install')).toHaveLength(1);
});

test('a drifted copy is flagged, with a link to the source folder', async ({ mount, page }) => {
  await mockCatalog(page, {
    search: catalogSearchResponse({ skills: [catalogSkill({ installed: true })] }),
    detail: installedCatalogSkillDetail({
      differs_from_installed: true,
      version: '2.0',
      installed_version: '0.1.0',
    }),
  });
  await mountCatalogTab(mount, page);

  await page.getByRole('button', { name: /Frontend Dev/ }).click();
  const dialog = page.getByRole('dialog');

  await expect(dialog).toContainText('Your copy differs');
  await expect(dialog).toContainText('v2.0');
  await expect(dialog).toContainText('v0.1.0');

  const link = dialog.getByRole('link', { name: /acme\/frontend-dev/ });
  await expect(link).toHaveAttribute(
    'href',
    'https://github.com/acme/frontend-dev/tree/HEAD/frontend-dev',
  );
  // Third-party destination — never opened with access to this window.
  await expect(link).toHaveAttribute('rel', /noopener/);
});

test('a copy identical to the registry says so', async ({ mount, page }) => {
  await mockCatalog(page, {
    search: catalogSearchResponse({ skills: [catalogSkill({ installed: true })] }),
    detail: installedCatalogSkillDetail(),
  });
  await mountCatalogTab(mount, page);

  await page.getByRole('button', { name: /Frontend Dev/ }).click();
  await expect(page.getByRole('dialog')).toContainText('Matches your copy');
});

test('the preview dates the skill, and survives a history GitHub would not give', async ({
  mount,
  page,
}) => {
  await mockCatalog(page, { detail: catalogSkillDetail() });
  await mountCatalogTab(mount, page);

  await page.getByRole('button', { name: /Frontend Dev/ }).click();
  const dialog = page.getByRole('dialog');

  await expect(dialog).toContainText('Apr 28, 2026');
  await expect(dialog).toContainText('Aug 15, 2026');
  await expect(dialog).toContainText('Clarify the steps');
});

test('a skill with no history still previews', async ({ mount, page }) => {
  await mockCatalog(page, {
    detail: catalogSkillDetail({
      created_at: null,
      last_modified_at: null,
      last_change_summary: null,
    }),
  });
  await mountCatalogTab(mount, page);

  await page.getByRole('button', { name: /Frontend Dev/ }).click();
  const dialog = page.getByRole('dialog');

  await expect(dialog).toContainText('Use React and Vite.');
  await expect(dialog).not.toContainText('Last changed');
});
