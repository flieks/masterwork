import { test, expect } from '@playwright/experimental-ct-react';
import { NewProjectDialog } from '~/features/projects/components/NewProjectDialog';
import { TestProviders } from './harness/TestProviders';

test('Create is disabled until a name is entered', async ({ mount, page }) => {
  await mount(
    <TestProviders>
      <NewProjectDialog open onOpenChange={() => {}} />
    </TestProviders>,
  );

  const create = page.getByRole('button', { name: 'Create project' });
  await expect(create).toBeDisabled();

  // Whitespace-only name is still invalid.
  const name = page.getByLabel('Name');
  await name.fill('   ');
  await expect(create).toBeDisabled();

  await name.fill('Deploy pipeline');
  await expect(create).toBeEnabled();

  await name.fill('');
  await expect(create).toBeDisabled();
});
