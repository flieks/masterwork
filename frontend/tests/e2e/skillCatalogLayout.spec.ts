import { test, expect } from '@playwright/test';

// Layout can only be asserted where real CSS runs, so this lives in E2E rather
// than in the component tests — playwright-ct.config.ts ships no Tailwind by
// design ("CT verifies behaviour, not styling").
test.describe('skill catalog: the preview dialog contains its content', () => {
  test.skip(!process.env.BACKEND_URL, 'requires a running backend (set BACKEND_URL)');

  // Real case: nvidia/skills ships one commit subject naming every synced skill,
  // which forced the dialog wider than the screen until `truncate` got min-w-0.
  const brutalSummary =
    'chore: sync skills (AIQ,CUDA-Q,DeepStream,Megatron-Core,NeMo MBridge,NeMo Platform,' +
    'NeMo Retriever,NemoClaw,accelerated-computing-cudf,aiq-deploy,cuda-graphs)';

  for (const viewport of [
    { width: 1400, height: 900, name: 'desktop' },
    { width: 375, height: 812, name: 'mobile' },
  ]) {
    test(`stays inside the viewport on ${viewport.name}`, async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });

      await page.route('**/api/v1/skills/catalog**', async (route) => {
        if (new URL(route.request().url()).pathname === '/api/v1/skills/catalog') {
          return route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({
              skills: [
                {
                  owner: 'acme',
                  repo: 'widgets',
                  skill: 'wide-load',
                  name: 'Wide Load',
                  description: 'A skill with punishing content.',
                  registry: 'skills_sh',
                  installs: 1,
                  license: 'MIT',
                  license_resolved: true,
                  url: 'https://github.com/acme/widgets',
                  installed: false,
                },
              ],
              errors: [],
            }),
          });
        }
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            owner: 'acme',
            repo: 'widgets',
            skill: 'wide-load',
            name: 'Wide Load',
            registry: 'skills_sh',
            license: 'MIT',
            all_rights_reserved: false,
            url: 'https://github.com/acme/widgets/tree/HEAD/wide-load',
            version: null,
            installed_version: null,
            created_at: '2026-04-28T09:17:37Z',
            last_modified_at: '2026-08-15T20:48:40Z',
            last_change_summary: brutalSummary,
            differs_from_installed: null,
            installed: false,
            installed_by_masterwork: false,
            // Frontmatter is kept verbatim; the body renders as markdown. The
            // unbroken run has nowhere to wrap — a long URL or a base64 blob.
            skill_md: [
              '---',
              'name: wide-load',
              'allowed-tools: ["Bash(git *)"]',
              '---',
              '',
              '# Wide Load',
              '',
              'x'.repeat(400),
              '',
              '| a | b |',
              '| --- | --- |',
              `| ${'wide'.repeat(60)} | y |`,
              '',
              '```',
              'z'.repeat(400),
              '```',
            ].join('\n'),
            files: [],
          }),
        });
      });

      await page.goto('/skills?view=catalog');
      await page.getByLabel('Search the skill catalog').fill('wide');
      await page.getByRole('button', { name: /Wide Load/ }).click();

      const dialog = page.getByRole('dialog');
      await expect(dialog).toBeVisible();
      // The dialog opens on a skeleton; measure only once the body has rendered.
      await expect(dialog.locator('.prose')).toBeVisible();
      // The markdown body renders its own h1, distinct from the dialog title.
      await expect(dialog.locator('.prose h1')).toHaveText('Wide Load');

      const box = await page.evaluate(() => {
        const el = document.querySelector('[role="dialog"]') as HTMLElement;
        const rect = el.getBoundingClientRect();
        const root = document.documentElement;
        return {
          // The dialog stayed 672px wide even while broken — its content was what
          // blew out. Measuring the dialog's own overflow catches both halves:
          // content that escapes the box, and content clipped out of reach.
          // Tables and code blocks are exempt by construction: prose gives them
          // overflow-x:auto, so they scroll inside their own width.
          contentOverflowsX: el.scrollWidth > el.clientWidth + 1,
          // Flex children in the scrolling column default to flex-shrink: 1, which
          // silently squashed the frontmatter block to a single line — the rest of
          // it stayed in the DOM but was unreachable.
          verticallySquashed: [...el.querySelectorAll('pre, .prose')]
            .filter((child) => {
              const cs = getComputedStyle(child);
              if (cs.overflowY === 'auto' || cs.overflowY === 'scroll') return false;
              return child.scrollHeight > child.clientHeight + 1;
            })
            .map((child) => `${child.tagName}@${child.clientHeight}/${child.scrollHeight}`),
          withinViewport: rect.left >= -1 && rect.right <= window.innerWidth + 1,
          pageScrollsX: root.scrollWidth > root.clientWidth,
          tallerThanScreen: rect.height > window.innerHeight,
        };
      });

      expect(box.contentOverflowsX).toBe(false);
      expect(box.verticallySquashed).toEqual([]);
      expect(box.withinViewport).toBe(true);
      expect(box.pageScrollsX).toBe(false);
      expect(box.tallerThanScreen).toBe(false);
    });
  }
});
