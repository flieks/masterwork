import { Badge } from '~/components/ui/badge';

export const CODEX_CONFIG_NOTE = 'Disabled in ~/.codex/config.toml — enable it there';

/** Why an asset is off: parked under .disabled/ (the default) or switched off in Codex's config. */
function disabledHint(disabledBy?: string | null): string {
  return disabledBy === 'codex-config'
    ? `${CODEX_CONFIG_NOTE}; masterwork never edits that file`
    : 'Parked under .disabled/ in its skills folder; no coding agent loads it until it is switched back on';
}

/** A skill that is still on disk but loaded by nobody. */
export function DisabledBadge({ disabledBy }: { disabledBy?: string | null }) {
  return (
    <Badge variant="muted" title={disabledHint(disabledBy)}>
      Disabled
    </Badge>
  );
}
