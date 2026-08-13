import { useState } from 'react';
import { Dialog, DialogContent, DialogTitle } from '~/components/ui/dialog';
import { mediaUrl, type EventImage } from '../media';

/**
 * The pictures a tool call answered with, as pictures.
 *
 * A screenshot's whole content is the image, and the JSON around it says only
 * that one was taken — so these sit above the payload rather than inside it,
 * and are the one thing in a row that shows without being expanded.
 */
export function EventImages({ sessionId, images }: { sessionId: string; images: EventImage[] }) {
  const [opened, setOpened] = useState<EventImage | null>(null);

  if (images.length === 0) return null;

  return (
    <>
      <ul className="mt-1.5 flex flex-wrap gap-2">
        {images.map((image) => (
          <li key={image.mediaId}>
            <button
              type="button"
              onClick={() => setOpened(image)}
              aria-label="Open image full size"
              className="block overflow-hidden rounded-md border bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <img
                src={mediaUrl(sessionId, image.mediaId)}
                alt=""
                loading="lazy"
                className="h-24 w-auto max-w-[16rem] object-cover object-left-top"
              />
            </button>
          </li>
        ))}
      </ul>

      <Dialog open={opened !== null} onOpenChange={(open) => !open && setOpened(null)}>
        {/* Sized by the picture, not by the dialog's default column. */}
        <DialogContent className="w-auto max-w-[90vw] p-2">
          {/* The image is the content; the title is here for the screen reader. */}
          <DialogTitle className="sr-only">Image from this tool call</DialogTitle>
          {opened ? (
            <img
              src={mediaUrl(sessionId, opened.mediaId)}
              alt=""
              className="max-h-[80vh] w-auto rounded"
            />
          ) : null}
        </DialogContent>
      </Dialog>
    </>
  );
}
