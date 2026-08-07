import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { MermaidView } from './MermaidView';
import { cn } from '~/lib/utils';

interface MarkdownViewProps {
  content: string;
  className?: string;
}

const MERMAID_CLASS = 'language-mermaid';

/** True for a hast <pre> whose only child is a <code class="language-mermaid">. */
function preWrapsMermaid(node: unknown): boolean {
  const el = node as { children?: Array<{ tagName?: string; properties?: { className?: unknown } }> };
  const first = el?.children?.[0];
  if (!first || first.tagName !== 'code') return false;
  const cls = first.properties?.className;
  return Array.isArray(cls) && cls.includes(MERMAID_CLASS);
}

const components: Components = {
  a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noopener noreferrer" />,
  // Unwrap mermaid blocks so the diagram <div> isn't nested inside a <pre>.
  pre: ({ node, children, ...props }) =>
    preWrapsMermaid(node) ? <>{children}</> : <pre {...props}>{children}</pre>,
  code: ({ node: _node, className, children, ...props }) => {
    if (className?.split(' ').includes(MERMAID_CLASS)) {
      return <MermaidView source={String(children).replace(/\n$/, '')} className="my-3" />;
    }
    return (
      <code className={className} {...props}>
        {children}
      </code>
    );
  },
};

/** Renders GitHub-flavoured markdown; ```mermaid fences render as diagrams. */
export function MarkdownView({ content, className }: MarkdownViewProps) {
  return (
    <div className={cn('prose', className)}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  );
}
