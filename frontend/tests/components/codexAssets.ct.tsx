import { test, expect, type Page, type Route } from '@playwright/experimental-ct-react';
import type { AssetDetail } from '~/api/generated';
import { SkillDetailApp } from './harness/SkillDetailApp';

/**
 * Codex assets at parity: a skill switched off in ~/.codex/config.toml, a
 * custom agent that is TOML rather than markdown, and a plugin skill.
 */

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET,POST,PUT,PATCH,DELETE,OPTIONS',
  'Access-Control-Allow-Headers': '*',
};

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json',
    headers: CORS,
    body: JSON.stringify(body),
  });
}

function detail(over: Partial<AssetDetail>): AssetDetail {
  return {
    id: 'codex:skill:imagegen',
    kind: 'skill',
    provider: 'codex',
    name: 'imagegen',
    title: 'imagegen',
    description: 'Generate images.',
    model: null,
    agents: ['codex'],
    path: '/Users/me/.codex/skills/imagegen/SKILL.md',
    created_at: '2026-08-01T10:00:00Z',
    updated_at: '2026-08-02T10:00:00Z',
    read_only: false,
    disabled: false,
    disabled_by: null,
    content: '---\nname: imagegen\n---\n# Imagegen\n',
    ...over,
  };
}

const CONFIG_OFF = detail({ disabled: true, disabled_by: 'codex-config', agents: [] });

const TOML_AGENT = detail({
  id: 'codex:agent:reviewer',
  kind: 'agent',
  name: 'reviewer',
  title: 'reviewer',
  description: 'Reviews diffs.',
  model: 'gpt-5.1-codex',
  path: '/Users/me/.codex/agents/reviewer.toml',
  content:
    'name = "reviewer"\n' +
    'description = "Reviews diffs."\n' +
    '# Not a heading, a TOML comment\n' +
    'developer_instructions = """\n---\nRead the diff first.\n"""\n',
});

const PLUGIN_SKILL = detail({
  id: 'codex-plugin:skill:figma:implement',
  provider: 'codex-plugin',
  name: 'figma:implement',
  title: 'implement',
  read_only: true,
  disabled: true,
  disabled_by: 'codex-config',
  agents: [],
  path: '/Users/me/.codex/plugins/cache/openai/figma/1.2.0/skills/implement/SKILL.md',
});

interface Mock {
  calls: string[];
  /** What GET answers per asset id; a toggle or save can replace an entry. */
  assets: Map<string, AssetDetail>;
}

async function mockAssets(
  page: Page,
  assets: AssetDetail[],
  opts: {
    onToggle?: (mock: Mock, enabled: boolean) => { status: number; body: unknown };
    onSave?: (mock: Mock, content: string) => { status: number; body: unknown };
  } = {},
): Promise<Mock> {
  const mock: Mock = { calls: [], assets: new Map(assets.map((a) => [a.id, a])) };
  await page.route('**/api/v1/**', async (route) => {
    const request = route.request();
    if (request.method() === 'OPTIONS') {
      await route.fulfill({ status: 204, headers: CORS, body: '' });
      return;
    }
    const path = decodeURIComponent(new URL(request.url()).pathname);
    mock.calls.push(`${request.method()} ${path}`);
    const id = path.replace('/api/v1/assets/', '').replace(/\/enabled$/, '');

    if (path.endsWith('/enabled') && opts.onToggle) {
      const { enabled } = request.postDataJSON() as { enabled: boolean };
      const { status, body } = opts.onToggle(mock, enabled);
      return json(route, body, status);
    }
    if (request.method() === 'PUT' && mock.assets.has(id) && opts.onSave) {
      const { content } = request.postDataJSON() as { content: string };
      const { status, body } = opts.onSave(mock, content);
      return json(route, body, status);
    }
    if (request.method() === 'GET' && mock.assets.has(id)) return json(route, mock.assets.get(id));
    if (path.endsWith('/diagram')) return json(route, { detail: 'not found' }, 404);
    return json(route, []);
  });
  return mock;
}

test.describe('a skill switched off in ~/.codex/config.toml', () => {
  test('locks the switch off and says where to turn it back on', async ({ mount, page }) => {
    const mock = await mockAssets(page, [CONFIG_OFF]);
    const component = await mount(<SkillDetailApp path="/skills/imagegen?p=codex" />);

    const toggle = component.getByRole('switch', { name: 'Enabled' });
    await expect(toggle).not.toBeChecked();
    await expect(toggle).toBeDisabled();
    await expect(
      component.getByText('Disabled in ~/.codex/config.toml — enable it there.'),
    ).toBeVisible();
    // The badges explain the config, not a .disabled/ folder that does not exist.
    await expect(
      component.getByTitle(/Disabled in ~\/\.codex\/config\.toml/).first(),
    ).toBeVisible();
    await expect(component.getByTitle(/Parked under \.disabled/)).toHaveCount(0);

    await component.locator('label', { hasText: 'Enabled' }).click({ force: true });
    expect(mock.calls.filter((c) => c.endsWith('/enabled'))).toHaveLength(0);
  });

  test('a 409 from a stale page shows the reason and brings the lock in', async ({
    mount,
    page,
  }) => {
    const parked = detail({ disabled: true, disabled_by: 'folder', agents: [] });
    const reason =
      'imagegen is switched off in ~/.codex/config.toml; set enabled = true for its [[skills.config]] entry there';
    await mockAssets(page, [parked], {
      onToggle: (mock) => {
        // config.toml changed since the page loaded: the refetch now sees the lock.
        mock.assets.set(CONFIG_OFF.id, CONFIG_OFF);
        return { status: 409, body: { detail: reason } };
      },
    });
    const component = await mount(<SkillDetailApp path="/skills/imagegen?p=codex" />);

    const toggle = component.getByRole('switch', { name: 'Enabled' });
    await expect(toggle).toBeEnabled();
    await component.locator('label', { hasText: 'Enabled' }).click();

    await expect(page.getByText("Couldn't enable it")).toBeVisible();
    await expect(page.getByText(reason)).toBeVisible();
    await expect(toggle).toBeDisabled();
    await expect(toggle).not.toBeChecked();
    await expect(
      component.getByText('Disabled in ~/.codex/config.toml — enable it there.'),
    ).toBeVisible();
  });
});

test.describe('a Codex custom agent is TOML', () => {
  test('its file renders verbatim as code, not as markdown', async ({ mount, page }) => {
    await mockAssets(page, [TOML_AGENT]);
    const component = await mount(<SkillDetailApp path="/agents/reviewer?p=codex" />);

    await expect(component.getByRole('heading', { name: 'reviewer', level: 1 })).toBeVisible();
    const source = component.getByLabel('TOML source');
    await expect(source).toContainText('developer_instructions = """');
    await expect(source).toContainText('# Not a heading, a TOML comment');
    // Markdown would have turned the comment into a heading and `---` into a rule,
    // and a YAML frontmatter split would have eaten the lines between the dashes.
    await expect(
      component.getByRole('heading', { name: 'Not a heading, a TOML comment' }),
    ).toHaveCount(0);
    await expect(component.getByText('Frontmatter', { exact: true })).toHaveCount(0);
    await expect(component.getByText('Codex only')).toBeVisible();
  });

  test('a 400 on save shows the validation message and keeps the draft', async ({
    mount,
    page,
  }) => {
    const reason = "~/.codex/agents/reviewer.toml is missing 'developer_instructions' (a string)";
    const mock = await mockAssets(page, [TOML_AGENT], {
      onSave: () => ({ status: 400, body: { detail: reason } }),
    });
    const component = await mount(<SkillDetailApp path="/agents/reviewer?p=codex" />);

    await component.getByRole('button', { name: 'Edit' }).click();
    const editor = component.getByLabel('TOML editor');
    await expect(editor).toBeVisible();
    const content = editor.locator('.cm-content');
    await expect(content).toContainText('name = "reviewer"');

    await content.fill('name = "reviewer"\ndescription = "Reviews diffs."\n');
    await component.getByRole('button', { name: 'Save' }).click();

    await expect(component.getByRole('alert')).toHaveText(reason);
    await expect(page.getByText('Save failed')).toBeVisible();
    // Still editing, with the rejected draft intact for the fix.
    await expect(content).toContainText('description = "Reviews diffs."');
    await expect(content).not.toContainText('developer_instructions');
    await expect(component.getByRole('button', { name: 'Save' })).toBeEnabled();
    expect(mock.calls).toContain('PUT /api/v1/assets/codex:agent:reviewer');
  });
});

test('a Codex plugin skill is read-only, badged, and off until config.toml enables it', async ({
  mount,
  page,
}) => {
  await mockAssets(page, [PLUGIN_SKILL]);
  const component = await mount(<SkillDetailApp path="/skills/figma:implement?p=codex-plugin" />);

  await expect(component.getByText('Read-only · plugin')).toBeVisible();
  await expect(component.getByText('codex plugin', { exact: true })).toBeVisible();
  await expect(component.getByRole('switch')).toHaveCount(0);
  await expect(component.getByRole('button', { name: 'Edit' })).toHaveCount(0);
  await expect(component.getByText('Not loaded by any agent')).toBeVisible();
  await expect(
    component.getByText('Disabled in ~/.codex/config.toml — enable it there.'),
  ).toBeVisible();
});
