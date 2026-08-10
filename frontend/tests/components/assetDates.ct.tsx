import { test, expect, type Page } from '@playwright/experimental-ct-react';
import type { AssetSummary } from '~/api/generated';
import { assetAge } from '~/features/assets/dates';
import { AssetDatesInline, AssetDatesStacked } from '~/features/assets/components/AssetDates';
import { AssetTable } from '~/features/assets/components/AssetTable';
import { TestProviders } from './harness/TestProviders';

/**
 * When an asset was written, versus when it was last touched.
 *
 * Three readings have to stay apart on screen: two real dates, one write that
 * was never edited, and a platform that records no birth time at all. The last
 * two are the ones a display quietly gets wrong — by printing one date twice,
 * or by filling an absent date in with the other one.
 */

/** The real shape of `claude:skill:code-review`: written in July, edited in August. */
const EDITED = { created: '2026-07-10T12:00:00.000Z', updated: '2026-08-07T12:00:00.000Z' };

/**
 * The real shape of a file written once. The stamps are NOT equal — birth time
 * and mtime land a few hundred ms apart — which is why equality cannot be the test.
 */
const WRITTEN_ONCE = { created: '2026-07-10T12:00:00.000Z', updated: '2026-07-10T12:00:00.408Z' };

// ---------------------------------------------------------------- the rule

test('a sub-second gap is one write; a real edit is two dates', () => {
  expect(assetAge(WRITTEN_ONCE.created, WRITTEN_ONCE.updated).state).toBe('written-once');
  expect(assetAge(EDITED.created, EDITED.updated).state).toBe('edited');
});

test('the written-once tolerance is a second, not a day', () => {
  const born = '2026-07-10T12:00:00.000Z';
  expect(assetAge(born, '2026-07-10T12:00:00.999Z').state).toBe('written-once');
  expect(assetAge(born, '2026-07-10T12:00:01.500Z').state).toBe('edited');
  // Same calendar day, hours apart: two facts, however the day label reads.
  expect(assetAge(born, '2026-07-10T20:00:00.000Z').state).toBe('edited');
});

test('a missing or unparseable creation date is unknown, never a date', () => {
  expect(assetAge(null, EDITED.updated)).toMatchObject({ state: 'unknown', created: null });
  expect(assetAge(undefined, EDITED.updated)).toMatchObject({ state: 'unknown', created: null });
  expect(assetAge('not-a-date', EDITED.updated)).toMatchObject({ state: 'unknown', created: null });
});

// ------------------------------------------------- the detail header line

test('detail header shows both dates, each labelled and distinguishable', async ({ mount }) => {
  const line = await mount(<AssetDatesInline {...EDITED} />);

  await expect(line).toContainText('Created Jul 10, 2026');
  await expect(line).toContainText('Updated Aug 7, 2026');

  // Two <time> elements carrying two different instants — not one date reused.
  const stamps = line.locator('time');
  await expect(stamps).toHaveCount(2);
  expect(await stamps.nth(0).getAttribute('datetime')).not.toBe(
    await stamps.nth(1).getAttribute('datetime'),
  );
});

test('a file written once states the write and that nothing followed it', async ({ mount }) => {
  const line = await mount(<AssetDatesInline {...WRITTEN_ONCE} />);

  await expect(line).toContainText('Created Jul 10, 2026');
  await expect(line).toContainText('Never edited');

  // The point of the collapse: the one date is printed once, not twice.
  const text = (await line.textContent()) ?? '';
  expect(text.match(/Jul 10, 2026/g)).toHaveLength(1);
  await expect(line).toHaveText('Created Jul 10, 2026 · Never edited');
});

test('an unknown creation date renders as unknown, not as the edit date', async ({ mount }) => {
  const line = await mount(<AssetDatesInline created={null} updated={EDITED.updated} />);

  await expect(line).toHaveText('Created — · Updated Aug 7, 2026');
  // No epoch, no today, and not the edit date borrowed to fill the hole.
  await expect(line).not.toContainText('1970');
  expect((await line.textContent())?.match(/Aug 7, 2026/g)).toHaveLength(1);
  // The absence is explained rather than left as a bare dash.
  await expect(line.getByTitle(/does not record a birth time/i)).toBeVisible();
});

test('an edit later the same day shows clock times so the dates cannot read as one', async ({
  mount,
}) => {
  const line = await mount(
    <AssetDatesInline created="2026-08-10T07:00:00.000Z" updated="2026-08-10T15:30:00.000Z" />,
  );

  const text = (await line.textContent()) ?? '';
  expect(text).toContain('Created Aug 10, 2026,');
  expect(text).toContain('Updated Aug 10, 2026,');
  // Same day, so the disambiguation has to come from the time, not the date.
  expect(text.match(/Aug 10, 2026, \d\d:\d\d/g)).toHaveLength(2);
  expect(new Set(text.match(/\d\d:\d\d/g))).toHaveProperty('size', 2);
});

// ------------------------------------------------------- the grid card

test('a grid card stacks the write and the edit as separate lines', async ({ mount }) => {
  const card = await mount(<AssetDatesStacked {...EDITED} />);

  await expect(card).toContainText('Created Jul 10, 2026');
  await expect(card).toContainText('Edited');
  await expect(card.locator('time')).toHaveCount(2);
});

test('a written-once card says so instead of repeating the date', async ({ mount }) => {
  const card = await mount(<AssetDatesStacked {...WRITTEN_ONCE} />);

  await expect(card).toContainText('Created Jul 10, 2026');
  await expect(card).toContainText('Never edited');
  expect((await card.textContent())?.match(/Jul 10, 2026/g)).toHaveLength(1);
});

test('a card with no creation date drops the line rather than showing a dash', async ({
  mount,
}) => {
  const card = await mount(<AssetDatesStacked created={null} updated={EDITED.updated} />);

  await expect(card).not.toContainText('Created');
  await expect(card).not.toContainText('—');
  await expect(card).not.toContainText('1970');
  await expect(card).toContainText('Edited');
});

// ----------------------------------------------------------- the table

function asset(id: string, dates: { created: string | null; updated: string }): AssetSummary {
  return {
    id,
    kind: 'skill',
    provider: 'claude',
    name: id,
    title: id,
    description: '',
    model: null,
    path: `/skills/${id}/SKILL.md`,
    created_at: dates.created,
    updated_at: dates.updated,
    read_only: false,
  } as AssetSummary;
}

/** The table pulls a usage rollup it does not need here; answer it with nothing. */
async function stubUsage(page: Page) {
  await page.route('**/api/v1/**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: { 'Access-Control-Allow-Origin': '*' },
      body: '[]',
    }),
  );
}

test('the table gives creation its own column beside the update', async ({ mount, page }) => {
  await stubUsage(page);

  await mount(
    <TestProviders>
      <AssetTable
        kind="skill"
        assets={[
          asset('code-review', EDITED),
          asset('alembic-heads', WRITTEN_ONCE),
          asset('on-linux', { created: null, updated: EDITED.updated }),
        ]}
      />
    </TestProviders>,
  );

  // The rows carry role="link", so the cells are addressed as elements, not roles.
  const headers = page.locator('th');
  await expect(headers.filter({ hasText: /^Created$/ })).toHaveCount(1);
  await expect(headers.filter({ hasText: /^Updated$/ })).toHaveCount(1);

  // Both dates on the row, in their own cells.
  const edited = page.locator('tbody tr').filter({ hasText: 'code-review' });
  await expect(edited.locator('td').nth(6)).toHaveText('Jul 10, 2026');
  await expect(edited.locator('td').nth(7)).toHaveText('Aug 7, 2026');

  // Written once: the date sits under Created, and Updated says what happened.
  const once = page.locator('tbody tr').filter({ hasText: 'alembic-heads' });
  await expect(once.locator('td').nth(6)).toHaveText('Jul 10, 2026');
  await expect(once.locator('td').nth(7)).toHaveText('Never edited');

  // No birth time: an empty cell under a header that names it, never a fake date.
  const unknown = page.locator('tbody tr').filter({ hasText: 'on-linux' });
  await expect(unknown.locator('td').nth(6)).toHaveText('—');
  await expect(unknown.locator('td').nth(7)).toHaveText('Aug 7, 2026');
});
