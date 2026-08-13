import type { CodingEvent } from '~/api/generated';
import { baseURL } from '~/api/client';

/**
 * The images inside an event payload.
 *
 * A screenshot tool answers with its image inline as base64, which no cap in
 * this stack can carry — so the hook writes the bytes to disk and leaves an
 * `image_ref` where they were. The reference is what reaches the browser; the
 * bytes come back from the media endpoint, one request per picture.
 */
export interface EventImage {
  mediaId: string;
  mediaType: string;
  bytes: number | null;
}

/** Deep enough for a tool response's content blocks, bounded against a cycle. */
const MAX_DEPTH = 12;

/** A row shows a strip, not a gallery. */
const MAX_IMAGES = 8;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function asImage(node: Record<string, unknown>): EventImage | null {
  if (node.type !== 'image_ref' || typeof node.media_id !== 'string') return null;
  return {
    mediaId: node.media_id,
    mediaType: typeof node.media_type === 'string' ? node.media_type : 'image/png',
    bytes: typeof node.bytes === 'number' ? node.bytes : null,
  };
}

function collect(value: unknown, into: EventImage[], depth: number): void {
  if (depth > MAX_DEPTH || into.length >= MAX_IMAGES) return;
  if (Array.isArray(value)) {
    for (const item of value) collect(item, into, depth + 1);
    return;
  }
  if (!isRecord(value)) return;
  const image = asImage(value);
  if (image) {
    // The same screenshot can appear twice in one payload — it is content
    // addressed, so twice is the same file and one thumbnail.
    if (!into.some((seen) => seen.mediaId === image.mediaId)) into.push(image);
    return;
  }
  for (const item of Object.values(value)) collect(item, into, depth + 1);
}

/** Every image referenced by one event, in payload order. */
export function eventImages(event: CodingEvent): EventImage[] {
  const found: EventImage[] = [];
  collect(event.payload, found, 0);
  return found;
}

/** Where the bytes of one referenced image are served from. */
export function mediaUrl(sessionId: string, mediaId: string): string {
  return `${baseURL}/api/v1/coding-sessions/${encodeURIComponent(sessionId)}/media/${encodeURIComponent(mediaId)}`;
}
