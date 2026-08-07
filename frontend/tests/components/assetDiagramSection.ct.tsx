import { test, expect, type Page } from '@playwright/experimental-ct-react';
import type { AssetDiagram } from '~/api/generated';
import { DiagramPanel } from '~/features/assets/components/AssetDiagramSection';

// The panel is a <details> collapsed by default — expand it before asserting on the body.
async function openPanel(page: Page) {
  await page.getByText(/^Diagram\b/).click();
}

// DiagramPanel is the presentational view — the three data states map to what
// the query returns (null = never generated 404, stale:false, stale:true).
function diagram(overrides: Partial<AssetDiagram> = {}): AssetDiagram {
  return {
    asset_id: 'claude:skill:frontend-dev',
    mermaid: 'flowchart TD\n  A[Trigger] --> B[Steps]',
    generated_at: new Date(Date.now() - 3_600_000).toISOString(),
    stale: false,
    ...overrides,
  };
}

test('none: offers a Generate button and no diagram', async ({ mount, page }) => {
  await mount(
    <DiagramPanel
      diagram={null}
      noun="skill"
      isPending={false}
      isGenerating={false}
      elapsedSeconds={0}
      onGenerate={() => {}}
    />,
  );

  await openPanel(page);
  await expect(page.getByText('No diagram yet.', { exact: false })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Generate diagram' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Regenerate' })).toHaveCount(0);
});

test('fresh: shows the generated caption and no stale hint', async ({ mount, page }) => {
  await mount(
    <DiagramPanel
      diagram={diagram({ stale: false })}
      noun="skill"
      isPending={false}
      isGenerating={false}
      elapsedSeconds={0}
      onGenerate={() => {}}
    />,
  );

  await openPanel(page);
  await expect(page.getByText(/^Generated/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Regenerate' })).toBeVisible();
  await expect(
    page.getByText('The file changed since this diagram was generated.'),
  ).toHaveCount(0);
});

test('stale: shows the amber hint and a Regenerate button', async ({ mount, page }) => {
  await mount(
    <DiagramPanel
      diagram={diagram({ stale: true })}
      noun="skill"
      isPending={false}
      isGenerating={false}
      elapsedSeconds={0}
      onGenerate={() => {}}
    />,
  );

  await openPanel(page);
  await expect(
    page.getByText('The file changed since this diagram was generated.'),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: 'Regenerate' })).toBeVisible();
});

test('generating: shows the elapsed-seconds busy indicator', async ({ mount, page }) => {
  await mount(
    <DiagramPanel
      diagram={null}
      noun="skill"
      isPending={false}
      isGenerating
      elapsedSeconds={5}
      onGenerate={() => {}}
    />,
  );

  await openPanel(page);
  await expect(page.getByText(/Generating diagram · 5s/)).toBeVisible();
});
