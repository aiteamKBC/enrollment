import { Fragment, type ReactNode } from 'react';
import { groupPointActivities } from '@/utils/pointActivities';
import type { JourneyComponent } from '@/utils/learnerJourney';

/** Children keep their own routes, availability and completion controls. */
export function PointActivityRows({ components, current, renderRow }: {
  components: JourneyComponent[];
  current: (component: JourneyComponent) => boolean;
  renderRow: (component: JourneyComponent) => ReactNode;
}) {
  return groupPointActivities(components).map(group => group.pointNumber === null
    ? <Fragment key={group.key}>{renderRow(group.activities[0])}</Fragment>
    : <li key={group.key}>
      <details key={group.activities.findIndex(current)} open={group.activities.some(current)} className="group">
        <summary className="cursor-pointer px-4 py-3 text-sm font-semibold text-primary-900">
          Point {group.pointNumber}
          <span className="ml-2 text-xs font-normal text-foreground-500">{group.activities.length} activities</span>
        </summary>
        <ul aria-label={`Point ${group.pointNumber} activities`} className="divide-y divide-emerald-100 border-t border-emerald-100">
          {group.activities.map(renderRow)}
        </ul>
      </details>
    </li>);
}
