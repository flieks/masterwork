import { useEffect, useRef, useState } from 'react';
import { usePrefersDark } from '~/lib/hooks';
import { cn } from '~/lib/utils';

interface MermaidViewProps {
  source: string;
  className?: string;
}

// Monotonic id per mounted diagram — mermaid.render needs a unique DOM id, and
// useId() emits colons that aren't valid CSS selectors.
let seq = 0;

/**
 * Renders Mermaid source to inline SVG. The `mermaid` library is heavy and
 * loaded lazily (dynamic import) so it never joins the main bundle. Theme
 * follows the OS colour scheme. Parse/render failures fall back to the raw
 * source in a bordered block — it never crashes and never renders blank.
 */
export function MermaidView({ source, className }: MermaidViewProps) {
  const dark = usePrefersDark();
  const [svg, setSvg] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const idRef = useRef(`mermaid-${seq++}`);

  useEffect(() => {
    let cancelled = false;
    const trimmed = source.trim();
    if (!trimmed) {
      setSvg(null);
      setFailed(false);
      return;
    }

    (async () => {
      try {
        const mermaid = (await import('mermaid')).default;
        mermaid.initialize({
          startOnLoad: false,
          theme: dark ? 'dark' : 'default',
          securityLevel: 'strict',
          suppressErrorRendering: true,
        });
        // parse() validates without touching the DOM, so an invalid diagram
        // rejects here instead of injecting an error graphic into the page.
        await mermaid.parse(trimmed);
        const { svg: rendered } = await mermaid.render(`${idRef.current}-${Date.now()}`, trimmed);
        if (!cancelled) {
          setSvg(rendered);
          setFailed(false);
        }
      } catch {
        if (!cancelled) {
          setSvg(null);
          setFailed(true);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [source, dark]);

  if (failed) {
    return (
      <div className={cn('overflow-hidden rounded-md border', className)}>
        <div className="border-b bg-muted/40 px-3 py-1.5 text-xs font-medium text-muted-foreground">
          Invalid diagram — showing source
        </div>
        <pre className="overflow-x-auto px-3 py-2 font-mono text-xs">{source}</pre>
      </div>
    );
  }

  if (svg == null) {
    // Rendering (or empty) — reserve a little space to avoid a layout jump.
    return <div className={cn('min-h-6', className)} aria-hidden />;
  }

  return (
    <div
      className={cn('mermaid-view flex justify-center overflow-x-auto', className)}
      role="img"
      aria-label="Diagram"
      // reason: mermaid returns a trusted SVG string, rendered with securityLevel 'strict'.
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}
