import { lazy, Suspense } from 'react';
import { cn } from '~/lib/utils';

export interface CodeEditorProps {
  value: string;
  onChange?: (value: string) => void;
  readOnly?: boolean;
  minHeight?: string;
  maxHeight?: string;
  className?: string;
  ariaLabel?: string;
}

// CodeMirror is heavy and only needed in edit/preview flows — load it on demand.
const CodeMirrorEditor = lazy(() => import('./CodeMirrorEditor'));

export function CodeEditor(props: CodeEditorProps) {
  return (
    <Suspense
      fallback={
        <div
          className={cn('animate-pulse rounded-md border bg-muted/40', props.className)}
          style={{ minHeight: props.minHeight ?? '24rem' }}
        />
      }
    >
      <CodeMirrorEditor {...props} />
    </Suspense>
  );
}
