import { useState } from 'react';
import { ChevronRight, FileWarning, PackageCheck } from 'lucide-react';
import type { EnvelopeAttempt } from '~/api/generated';
import { Badge } from '~/components/ui/badge';
import { formatJson, truncateForDisplay } from '~/lib/json';
import { cn } from '~/lib/utils';
import { isRecovered, sortAttempts } from '../evidence';

/**
 * What the agent actually returned, attempt by attempt. The row that matters is
 * the one that did not parse: its error and the reply it was read out of are
 * the only things that can tell you how to fix the prompt, so that row opens
 * itself and the rest stay folded.
 */
export function EnvelopeAttemptList({ envelopes }: { envelopes: EnvelopeAttempt[] }) {
  return (
    <ol className="flex flex-col gap-2">
      {sortAttempts(envelopes).map((envelope) => (
        <li key={envelope.id}>
          <AttemptRow envelope={envelope} />
        </li>
      ))}
    </ol>
  );
}

function AttemptRow({ envelope }: { envelope: EnvelopeAttempt }) {
  const body = formatJson(envelope.body);
  const raw = envelope.raw_text;
  const recovered = isRecovered(envelope);

  return (
    <div
      className={cn(
        'rounded-md border p-2.5',
        envelope.parsed ? 'bg-muted/20' : 'border-red-500/40 bg-red-500/5',
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-[11px] uppercase tracking-wide text-muted-foreground">
          Attempt {envelope.attempt}
        </span>
        {envelope.role ? (
          <Badge variant="outline" className="font-mono text-[10px]">
            {envelope.role}
          </Badge>
        ) : null}
        {envelope.parsed ? (
          <Badge variant="success" className="gap-1">
            <PackageCheck className="size-3" />
            parsed
          </Badge>
        ) : (
          <Badge className="gap-1 border-transparent bg-red-500/15 text-red-700 dark:text-red-400">
            <FileWarning className="size-3" />
            did not parse
          </Badge>
        )}
        {envelope.status ? (
          <span className="font-mono text-xs text-muted-foreground">
            status <span className="font-medium text-foreground">{envelope.status}</span>
          </span>
        ) : null}
        {recovered ? (
          <Badge
            variant="muted"
            className="px-1 py-0 text-[10px]"
            title="Rebuilt from the stored event stream rather than reported by the producer."
          >
            recovered
          </Badge>
        ) : null}
      </div>

      {envelope.parse_error ? (
        <p className="mt-1.5 whitespace-pre-wrap break-words text-sm">
          <span className="text-red-700 dark:text-red-400">Parse error: </span>
          {envelope.parse_error}
        </p>
      ) : null}

      {body || raw ? (
        <div className="mt-2 flex flex-col gap-1.5">
          {body ? <Disclosure label="Envelope body" text={body} /> : null}
          {raw ? (
            <Disclosure
              label={`Raw reply (${raw.length.toLocaleString()} characters)`}
              text={truncateForDisplay(raw)}
              // The failed parse is the row people came for — open it.
              defaultOpen={!envelope.parsed}
            />
          ) : null}
        </div>
      ) : (
        <p className="mt-1.5 text-xs italic text-muted-foreground">
          {recovered
            ? 'No body or raw reply recorded — this attempt was rebuilt from the event stream, which never carried them.'
            : 'No body or raw reply recorded for this attempt.'}
        </p>
      )}
    </div>
  );
}

function Disclosure({
  label,
  text,
  defaultOpen = false,
}: {
  label: string;
  text: string;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex items-center gap-1.5 rounded text-xs text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <ChevronRight
          aria-hidden="true"
          className={cn('size-3.5 transition-transform', open && 'rotate-90')}
        />
        {label}
      </button>
      {open ? (
        // Wrapped, not side-scrolled: a raw reply is prose, and the panel is
        // narrow enough that a horizontal scrollbar would hide half of it.
        <pre className="mt-1.5 max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-md border bg-muted/40 p-3 text-xs leading-relaxed">
          {text}
        </pre>
      ) : null}
    </div>
  );
}
