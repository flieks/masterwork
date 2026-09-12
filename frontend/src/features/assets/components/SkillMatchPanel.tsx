import { useState } from 'react';
import { useAtom } from 'jotai';
import type { SkillMatch } from '~/api/generated';
import { apiErrorMessage } from '~/api/client';
import { Button } from '~/components/ui/button';
import { Textarea } from '~/components/ui/textarea';
import { matchInstalledSkillsMutationAtom } from '../queries';

interface SkillMatchPanelProps {
  onMatches: (matches: SkillMatch[]) => void;
}

/** Describe-to-find over the installed skills — fires on Find, never on a
 *  keystroke, since each search spends a real model call. */
export function SkillMatchPanel({ onMatches }: SkillMatchPanelProps) {
  const [query, setQuery] = useState('');
  const [{ mutateAsync: findMatches, isPending, isError, error }] = useAtom(
    matchInstalledSkillsMutationAtom,
  );

  async function handleFind() {
    const trimmed = query.trim();
    if (!trimmed) return;
    const result = await findMatches(trimmed);
    onMatches(result.matches);
  }

  return (
    <div className="flex flex-col gap-2 rounded-lg border bg-card p-3">
      <Textarea
        aria-label="Describe what you need"
        placeholder="e.g. reviewing a React PR for accessibility issues…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        rows={2}
      />
      <div className="flex items-center justify-between gap-2">
        {isError ? (
          <p className="text-xs text-destructive">{apiErrorMessage(error)}</p>
        ) : (
          <span />
        )}
        <Button size="sm" onClick={() => void handleFind()} disabled={!query.trim() || isPending}>
          {isPending ? 'Finding…' : 'Find'}
        </Button>
      </div>
    </div>
  );
}
