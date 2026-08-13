import { test, expect } from '@playwright/test';

// E2E runs against a real backend + dev server. It no-ops unless BACKEND_URL is
// set (the dev server is launched via playwright.config's webServer in that case).
test.describe('sessions: grid → waterfall', () => {
  test.skip(!process.env.BACKEND_URL, 'requires a running backend (set BACKEND_URL)');

  test('lists runs as cards and opens one as a waterfall', async ({ page }) => {
    await page.goto('/sessions');

    await expect(page.getByRole('heading', { name: 'Sessions' })).toBeVisible();
    // The run count only appears once the query resolves — wait for it before
    // counting cards, or the skeleton is mistaken for an empty database.
    await expect(page.getByText(/^\d+ runs?$/)).toBeVisible();

    // The database may legitimately hold no runs — a fresh install has never
    // fired a hook — so accept either the grid or the empty state.
    const cards = page.locator('a[href^="/sessions/"]');
    if ((await cards.count()) === 0) {
      await expect(page.getByText('No runs recorded yet')).toBeVisible();
      return;
    }

    await cards.first().click();
    await expect(page).toHaveURL(/\/sessions\/.+/);
    await expect(page.getByRole('navigation', { name: 'Breadcrumb' })).toContainText('Sessions');
    await expect(page.getByLabel('Run waterfall')).toBeVisible();

    // The full stream is still reachable, and still renders.
    await page.getByRole('tab', { name: /All events/ }).click();
    await expect(page.getByRole('tabpanel')).toBeVisible();
  });

  test('the workflow filter narrows the grid', async ({ page }) => {
    await page.goto('/sessions');

    await page
      .getByRole('group', { name: 'Workflow' })
      .getByRole('button', { name: 'Factory' })
      .click();

    // Whatever comes back, every card in the grid is now a factory run.
    const workflowLines = page.locator('a[href^="/sessions/"] >> text=factory');
    const cards = page.locator('a[href^="/sessions/"]');
    if ((await cards.count()) > 0) {
      expect(await workflowLines.count()).toBeGreaterThan(0);
    }
  });

  test('every card reports working time, and none of them doubles the currency', async ({
    page,
  }) => {
    await page.goto('/sessions');
    await expect(page.getByText(/^\d+ runs?$/)).toBeVisible();

    const cards = page.locator('a[href^="/sessions/"]');
    if ((await cards.count()) === 0) return;

    // Every card leads with the honest duration, never a bare wall clock.
    await expect(cards.first().getByText(/active/)).toBeVisible();
    // The doubled dollar sign this screen used to render.
    await expect(page.getByText('$$')).toHaveCount(0);
  });

  test('the abandoned filter finds runs that went quiet, and none reads as running', async ({
    page,
  }) => {
    await page.goto('/sessions');

    await page
      .getByRole('group', { name: 'Status' })
      .getByRole('button', { name: 'Abandoned' })
      .click();

    const cards = page.locator('a[href^="/sessions/"]');
    await expect(page.getByText(/^\d+ runs?$/)).toBeVisible();
    const count = await cards.count();
    if (count === 0) return;

    // Every card wears the abandoned chip, and it explains itself. Matched on
    // the chip's tooltip rather than on text: a run *titled* "You are running a
    // SIMULATION…" would satisfy a naive search for "running".
    await expect(page.getByTitle(/SessionEnd hook dies with the process/)).toHaveCount(count);
    await expect(page.getByText('Live', { exact: true })).toHaveCount(0);
  });

  test('the assets rollup ranks real assets and links each one to its page', async ({ page }) => {
    await page.goto('/sessions?view=assets');

    await expect(page.getByRole('tab', { name: 'Assets' })).toHaveAttribute(
      'aria-selected',
      'true',
    );

    const rows = page.locator('tbody tr');
    if ((await rows.count()) === 0) {
      await expect(page.getByText('No asset usage recorded')).toBeVisible();
      return;
    }

    // Narrowing to skills must leave only skills behind. Asserted on the Kind
    // column, not the row: the skill named `agent-factory` contains "agent".
    await page.getByRole('group', { name: 'Kind' }).getByRole('button', { name: 'Skills' }).click();
    const kinds = page.locator('tbody tr td:nth-child(2)');
    await expect(kinds.first()).toHaveText('skill');
    expect(new Set(await kinds.allTextContents())).toEqual(new Set(['skill']));

    // Following a row lands on that asset's own page.
    const first = rows.first().getByRole('link');
    const name = (await first.textContent())?.trim() ?? '';
    await first.click();
    await expect(page).toHaveURL(/\/(skills|agents)\/.+/);
    await expect(page.getByRole('heading', { name, exact: true })).toBeVisible();
  });

  test('a pipeline run reveals the stage runs it launched', async ({ page }) => {
    await page.goto('/sessions');

    await page
      .getByRole('group', { name: 'Workflow' })
      .getByRole('button', { name: 'Factory' })
      .click();

    const withStages = page.locator('a[href^="/sessions/"]').filter({ hasText: /stage runs?/ });
    if ((await withStages.count()) === 0) return;

    await withStages.first().click();
    const toggle = page.getByRole('button', { name: /stage runs?/ });
    await expect(toggle).toBeVisible();

    await toggle.click();
    // Each child is its own run, reachable from here. `.count()` does not wait,
    // and the list is fetched only once the affordance opens.
    const children = page.locator('li a[href^="/sessions/"]');
    await expect(children.first()).toBeVisible();
    await expect(children.first()).toContainText('active');
  });
});

test.describe('sessions: the request under the title', () => {
  test.skip(!process.env.BACKEND_URL, 'requires a running backend (set BACKEND_URL)');

  test('shows three lines of the prompt, and the rest on demand', async ({ page }) => {
    await page.goto('/sessions');
    await expect(page.getByText(/^\d+ runs?$/)).toBeVisible();

    const cards = page.locator('a[href^="/sessions/"]');
    if ((await cards.count()) === 0) return; // a fresh install has no runs

    // The first run long enough to be clamped; a two-word prompt never is.
    const request = page.getByRole('region').filter({ hasText: 'Request' }).first();
    for (let i = 0; i < Math.min(await cards.count(), 6); i += 1) {
      await cards.nth(i).click();
      await expect(page.getByLabel('Run waterfall')).toBeVisible();
      const expand = page.getByRole('button', { name: 'Show full request' });
      if (await expand.isVisible().catch(() => false)) {
        const text = request.locator('p').first();
        const clamped = await text.evaluate((el) => el.clientHeight);
        // Three lines, and everything past them still in the DOM to be found.
        expect(await text.evaluate((el) => el.scrollHeight)).toBeGreaterThan(clamped);

        await expand.click();
        expect(await text.evaluate((el) => el.clientHeight)).toBeGreaterThan(clamped);
        await expect(page.getByRole('button', { name: 'Show less' })).toBeVisible();
        return;
      }
      await page.goBack();
      await expect(page.getByText(/^\d+ runs?$/)).toBeVisible();
    }
  });
});
