import { Badge } from '~/components/ui/badge';

interface LicenseBadgeProps {
  /** Optional on the wire; an absent license is treated as no license, never as permissive. */
  license: string | null | undefined;
  /** False for a skills.sh search hit — license shows "unknown" until previewed, never "unlicensed". */
  resolved?: boolean;
}

/** A resolved null license means all rights reserved — made visibly distinct from a real SPDX id. */
export function LicenseBadge({ license, resolved = true }: LicenseBadgeProps) {
  if (!resolved) {
    return <Badge variant="outline">License unknown</Badge>;
  }
  if ((license ?? null) === null) {
    return <Badge variant="destructive">No license — all rights reserved</Badge>;
  }
  return <Badge variant="secondary">{license}</Badge>;
}
