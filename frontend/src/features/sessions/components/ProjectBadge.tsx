import { FolderGit2 } from 'lucide-react';
import type { CodingSession } from '~/api/generated';
import { cn } from '~/lib/utils';
import { projectTint, sessionLabel } from '../queries';

/**
 * Which app a run happened in — the first thing to know about a run, so it is
 * a colour before it is a word. The tint is hashed from the name, so the same
 * project is the same colour on every card and on its detail page.
 */
export function ProjectBadge({
  session,
  size = 'sm',
  className,
}: {
  session: CodingSession;
  size?: 'sm' | 'lg';
  className?: string;
}) {
  const project = sessionLabel(session);

  return (
    <span
      data-project={project}
      title={session.cwd ? `Project ${project} — ${session.cwd}` : `Project ${project}`}
      className={cn(
        'inline-flex min-w-0 max-w-full items-center gap-1 rounded-md border font-mono font-medium',
        size === 'lg' ? 'px-2 py-0.5 text-xs' : 'px-1.5 py-px text-[11px]',
        projectTint(project),
        className,
      )}
    >
      <FolderGit2
        className={cn('shrink-0', size === 'lg' ? 'size-3.5' : 'size-3')}
        aria-hidden="true"
      />
      <span className="truncate">{project}</span>
    </span>
  );
}
