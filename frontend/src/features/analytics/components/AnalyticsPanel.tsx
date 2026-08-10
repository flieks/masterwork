import { useAtom } from 'jotai';
import { analyticsIncludeChildrenAtom, analyticsIncludeInspectionAtom } from '../queries';
import { AnalyticsFilters } from './AnalyticsFilters';
import { GateStatsSection } from './GateStatsSection';
import { ModelStatsSection } from './ModelStatsSection';
import { RoleStatsSection } from './RoleStatsSection';
import { RunTrendSection } from './RunTrendSection';

/**
 * Everything recorded across every run, read as four aggregates of one
 * population — and meant to answer one question: which agent instruction
 * should I fix next?
 *
 * They stack in one scrolling column rather than hiding behind sub-tabs,
 * because the four are one argument: the gate that fails names the fix, the
 * role table says whose instruction it is, the trend says whether it is getting
 * worse and the model table says what it costs. Answering by clicking through
 * four tabs would also make it easy to compare numbers drawn from different
 * filters, which is exactly what the shared filter bar exists to prevent.
 */
export function AnalyticsPanel() {
  return (
    <div className="flex min-w-0 flex-col gap-8">
      <div className="flex flex-col gap-2">
        <AnalyticsFilters />
        <PopulationNote />
      </div>

      <GateStatsSection />
      <RoleStatsSection />
      <RunTrendSection />
      <ModelStatsSection />
    </div>
  );
}

/**
 * Who is being counted, in a sentence. The two exclusions are on by default and
 * were previously invisible; a reader who cannot see them has no way to know
 * the numbers are narrower than "everything".
 */
function PopulationNote() {
  const [includeInspection] = useAtom(analyticsIncludeInspectionAtom);
  const [includeChildren] = useAtom(analyticsIncludeChildrenAtom);

  return (
    <p className="max-w-3xl text-xs leading-relaxed text-muted-foreground">
      All four tables read the same population, so their numbers can be compared.{' '}
      {includeInspection
        ? "Counting masterwork's own analysis runs — every figure below is partly a measure of masterwork inspecting assets."
        : "Masterwork's own analysis runs are left out."}{' '}
      {includeChildren
        ? 'Runs launched by another run are counted in their own right, so a pipeline stage appears both here and inside its parent.'
        : 'Runs launched by another run are folded into the parent that launched them, so no stage is counted twice.'}
    </p>
  );
}
