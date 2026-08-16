import type { WorkItem } from '~/api/generated';

/**
 * Item vocabulary, kept free of the API client so it can be reasoned about (and
 * imported) without pulling axios or `import.meta.env` in. Re-exported from
 * `queries.ts`, which is the feature's surface.
 */

export interface WorkItemNode {
  item: WorkItem;
  children: WorkItemNode[];
}

/**
 * Assignee filter sentinel: matches items whose `assigned_to` equals the
 * owning source's `owner_display_name` (the PAT owner's DevOps identity).
 */
export const ASSIGNEE_ME = '@Me';

/** `owner_display_name` per work-source id; a source with no owner yet matches nobody. */
export type OwnerNames = Record<string, string | null>;

export interface WorkItemFilters {
  /** The full iteration string, or null for every sprint. */
  iteration: string | null;
  /** A display name, ASSIGNEE_ME, or null for everyone. */
  assignee: string | null;
  /** Case-insensitive substring on the title; empty matches everything. */
  query: string;
}

/** `external_id` is unique per source, not globally — two orgs share numbers. */
function itemKey(sourceId: string, externalId: number): string {
  return `${sourceId}:${externalId}`;
}

/** True when following parent_external_id from `item` comes back around. */
function hasParentLoop(item: WorkItem, nodes: Map<string, WorkItemNode>): boolean {
  const seen = new Set<number>([item.external_id]);
  let cursor = item.parent_external_id;
  while (cursor !== null) {
    if (seen.has(cursor)) return true;
    seen.add(cursor);
    cursor = nodes.get(itemKey(item.source_id, cursor))?.item.parent_external_id ?? null;
  }
  return false;
}

/**
 * Nest items under the loaded item their `parent_external_id` names. An item
 * whose parent was not fetched stays top-level, as does a member of a parent
 * loop — which would otherwise strand its whole ring off the tree and lose it.
 */
export function buildWorkItemTree(items: WorkItem[]): WorkItemNode[] {
  const nodes = new Map<string, WorkItemNode>();
  for (const item of items) {
    nodes.set(itemKey(item.source_id, item.external_id), { item, children: [] });
  }

  const roots: WorkItemNode[] = [];
  for (const item of items) {
    const node = nodes.get(itemKey(item.source_id, item.external_id));
    if (!node) continue;
    const parent =
      item.parent_external_id === null
        ? undefined
        : nodes.get(itemKey(item.source_id, item.parent_external_id));
    if (parent && !hasParentLoop(item, nodes)) parent.children.push(node);
    else roots.push(node);
  }
  return roots;
}

/**
 * Prune to the items a filter admits. A parent that misses is kept as the group
 * header for a child that hits — dropping it would orphan the match.
 */
export function filterWorkItemTree(
  nodes: WorkItemNode[],
  filters: WorkItemFilters,
  owners: OwnerNames,
): WorkItemNode[] {
  if (!hasActiveFilters(filters)) return nodes;
  const query = filters.query.trim().toLowerCase();

  const prune = (node: WorkItemNode): WorkItemNode | null => {
    const children = node.children
      .map(prune)
      .filter((child): child is WorkItemNode => child !== null);
    const matches =
      (filters.iteration === null || node.item.iteration === filters.iteration) &&
      matchesAssignee(node.item, filters.assignee, owners) &&
      (query === '' || node.item.title.toLowerCase().includes(query));
    if (!matches && children.length === 0) return null;
    return { item: node.item, children };
  };

  return nodes.map(prune).filter((node): node is WorkItemNode => node !== null);
}

function matchesAssignee(item: WorkItem, assignee: string | null, owners: OwnerNames): boolean {
  if (assignee === null) return true;
  if (assignee === ASSIGNEE_ME) {
    const owner = owners[item.source_id];
    return !!owner && item.assigned_to === owner;
  }
  return item.assigned_to === assignee;
}

export function hasActiveFilters(filters: WorkItemFilters): boolean {
  return filters.iteration !== null || filters.assignee !== null || filters.query.trim() !== '';
}

export function countNodes(nodes: WorkItemNode[]): number {
  return nodes.reduce((total, node) => total + 1 + countNodes(node.children), 0);
}

/** Every sprint the loaded items mention, as the full iteration path. */
export function sprintOptions(items: WorkItem[]): string[] {
  const seen = new Set<string>();
  for (const item of items) if (item.iteration) seen.add(item.iteration);
  return [...seen].sort();
}

/** Every assignee the loaded items name, for the filter dropdown. */
export function assigneeOptions(items: WorkItem[]): string[] {
  const seen = new Set<string>();
  for (const item of items) if (item.assigned_to) seen.add(item.assigned_to);
  return [...seen].sort();
}

/** `widgets\2026 Q3.3` reads as `2026 Q3.3`; the full path stays in a title. */
export function sprintLabel(iteration: string): string {
  const cut = iteration.indexOf('\\');
  return cut === -1 ? iteration : iteration.slice(cut + 1);
}
