import { NavLink, Outlet } from 'react-router-dom';
import { Boxes, Bot, MessageSquare, FolderKanban, FileText } from 'lucide-react';
import { cn } from '~/lib/utils';

const NAV = [
  { to: '/skills', label: 'Skills', icon: Boxes },
  { to: '/agents', label: 'Agents', icon: Bot },
  { to: '/projects', label: 'Projects', icon: FolderKanban },
  { to: '/instructions', label: 'CLAUDE.md', icon: FileText },
  { to: '/chat', label: 'Chat', icon: MessageSquare },
];

export function Layout() {
  return (
    <div className="flex h-screen">
      <nav className="flex w-56 shrink-0 flex-col border-r bg-card">
        <div className="flex items-center gap-2 px-4 py-4">
          <span className="flex size-7 items-center justify-center rounded-md bg-primary text-xs font-bold text-primary-foreground">
            AM
          </span>
          <span className="font-semibold tracking-tight">Masterwork</span>
        </div>

        <ul className="flex-1 space-y-1 px-2">
          {NAV.map(({ to, label, icon: Icon }) => (
            <li key={to}>
              <NavLink
                to={to}
                className={({ isActive }) =>
                  cn(
                    'flex items-center gap-2 rounded-md px-3 py-2 text-sm transition-colors',
                    isActive
                      ? 'bg-secondary font-medium text-secondary-foreground'
                      : 'text-muted-foreground hover:bg-accent hover:text-foreground',
                  )
                }
              >
                <Icon className="size-4" />
                {label}
              </NavLink>
            </li>
          ))}
        </ul>

        <div className="border-t px-4 py-3 text-[11px] leading-relaxed text-muted-foreground">
          Local developer tool. Reads skills &amp; agents from{' '}
          <code className="font-mono">~/.claude</code>.
        </div>
      </nav>

      <main className="min-w-0 flex-1 overflow-y-auto">
        <Outlet />
      </main>
    </div>
  );
}
