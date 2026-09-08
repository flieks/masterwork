import { Badge } from '~/components/ui/badge';

/** A skill parked under its folder's .disabled/ — still here, loaded by nobody. */
export function DisabledBadge() {
  return (
    <Badge
      variant="muted"
      title="Parked under .disabled/ in its skills folder; no coding agent loads it until it is switched back on"
    >
      Disabled
    </Badge>
  );
}
