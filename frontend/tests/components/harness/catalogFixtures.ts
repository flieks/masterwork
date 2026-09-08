import type {
  CatalogSearchResponse,
  CatalogSkill,
  CatalogSkillDetail,
  CatalogSourceError,
  InstalledSkill,
  UpstreamCheckResult,
} from '~/api/generated';

/** A licensed skills.sh hit — the common case. */
export function catalogSkill(overrides: Partial<CatalogSkill> = {}): CatalogSkill {
  return {
    owner: 'acme',
    repo: 'frontend-dev',
    skill: 'frontend-dev',
    name: 'Frontend Dev',
    description: 'React frontend guidelines.',
    registry: 'skills_sh',
    installs: 42,
    license: 'MIT',
    license_resolved: true,
    url: 'https://github.com/acme/frontend-dev',
    installed: false,
    ...overrides,
  };
}

/** An unlicensed GitHub hit — license resolved to null, meaning all rights reserved. */
export function unlicensedCatalogSkill(overrides: Partial<CatalogSkill> = {}): CatalogSkill {
  return catalogSkill({
    owner: 'other',
    repo: 'beta-tools',
    skill: 'beta-tools',
    name: 'Beta Tools',
    description: 'Experimental automation helpers.',
    registry: 'github',
    installs: null,
    license: null,
    license_resolved: true,
    url: 'https://github.com/other/beta-tools',
    ...overrides,
  });
}

export function catalogSourceError(
  overrides: Partial<CatalogSourceError> = {},
): CatalogSourceError {
  return {
    registry: 'github',
    message: 'GitHub rate limit exceeded',
    ...overrides,
  };
}

export function catalogSearchResponse(
  overrides: Partial<CatalogSearchResponse> = {},
): CatalogSearchResponse {
  return {
    skills: [catalogSkill(), unlicensedCatalogSkill()],
    errors: [],
    ...overrides,
  };
}

export function catalogSkillDetail(
  overrides: Partial<CatalogSkillDetail> = {},
): CatalogSkillDetail {
  return {
    owner: 'acme',
    repo: 'frontend-dev',
    skill: 'frontend-dev',
    name: 'Frontend Dev',
    registry: 'skills_sh',
    license: 'MIT',
    all_rights_reserved: false,
    url: 'https://github.com/acme/frontend-dev/tree/HEAD/frontend-dev',
    version: null,
    installed_version: null,
    created_at: '2026-04-28T09:17:37Z',
    last_modified_at: '2026-08-15T20:48:40Z',
    last_change_summary: 'Clarify the steps',
    differs_from_installed: null,
    installed: false,
    installed_by_masterwork: false,
    skill_md: '# Frontend Dev\n\nUse React and Vite.',
    files: [],
    ...overrides,
  };
}

export function unlicensedCatalogSkillDetail(
  overrides: Partial<CatalogSkillDetail> = {},
): CatalogSkillDetail {
  return catalogSkillDetail({
    owner: 'other',
    repo: 'beta-tools',
    skill: 'beta-tools',
    name: 'Beta Tools',
    registry: 'github',
    license: null,
    all_rights_reserved: true,
    skill_md: '# Beta Tools\n\nExperimental helpers with no license.',
    ...overrides,
  });
}

export function installedSkill(overrides: Partial<InstalledSkill> = {}): InstalledSkill {
  return {
    asset_id: 'claude:skill:frontend-dev',
    name: 'frontend-dev',
    owner: 'acme',
    repo: 'frontend-dev',
    license: 'MIT',
    registry: 'skills_sh',
    installed_at: '2026-08-20T09:00:00Z',
    source_url: 'https://github.com/acme/frontend-dev/tree/HEAD/frontend-dev',
    installed_sha: '1111111111111111111111111111111111111111',
    root_path: 'frontend-dev',
    last_checked_at: null,
    upstream_sha: null,
    drift_status: null,
    ...overrides,
  };
}

/** The source moved on: a SKILL.md diff plus one new companion file. */
export function upstreamCheckResult(
  overrides: Partial<UpstreamCheckResult> = {},
): UpstreamCheckResult {
  return {
    name: 'frontend-dev',
    status: 'upstream_changed',
    checked_at: '2026-09-08T10:00:00Z',
    source_url: 'https://github.com/acme/frontend-dev/tree/HEAD/frontend-dev',
    installed_sha: '1111111111111111111111111111111111111111',
    upstream_sha: '2222222222222222222222222222222222222222',
    upstream_last_modified_at: '2026-08-15T20:48:40Z',
    upstream_last_change_summary: 'Clarify the steps',
    skill_md_diff:
      '--- SKILL.md (installed)\n+++ SKILL.md (upstream)\n@@ -1,3 +1,3 @@\n # Frontend Dev\n \n-Use React.\n+Use React and Vite.\n',
    other_changes: [{ path: 'reference.md', change: 'added' }],
    ...overrides,
  };
}

/** Already present under the skills root — install would collide with it. */
export function installedCatalogSkillDetail(
  overrides: Partial<CatalogSkillDetail> = {},
): CatalogSkillDetail {
  return catalogSkillDetail({
    installed: true,
    installed_by_masterwork: true,
    differs_from_installed: false,
    ...overrides,
  });
}
