import { dateLabel, type AcademicModule, type Result, type YourModulesData } from './model';
import { cohortLanes, cohortRows, COHORT_LABEL_WIDTH, timelineAxis } from './timelineLayout';
import styles from './ModuleTimeline.module.css';

/** One row per visible cohort; every genuine delivery retains its own block. */
export function ModuleTimeline({ data, modules, results }: {
  data: YourModulesData; modules: AcademicModule[]; results: Map<string, Result>;
}) {
  const home = data.homeCohort;
  if (!home) return null;
  const rows = cohortRows(data, modules, results);
  const axis = timelineAxis(rows, home);
  return <section aria-label="Module Timeline" className="mt-6 rounded-2xl border border-purple-200">
    <div className="rounded-t-2xl bg-purple-100 p-4">
      <h3 className="font-semibold text-purple-950">Module Timeline</h3>
      <p className="mt-1 text-xs">Home cohort: {home.name} · {dateLabel(home.start)} – {dateLabel(home.end)}</p>
      <p className="mt-2 text-xs">Home offerings are purple; eligible alternatives are lavender. Group labels are informational. No booking or assignment is made here.</p>
    </div>
    {!rows.length ? <p className="p-4 text-sm text-foreground-600">No published schedule is available under the applicable cohort rules.</p> :
      <div className={styles.scroll} role="region" aria-label="Scrollable cohort timeline" tabIndex={0}>
        <table className={styles.timeline} aria-label="Module offerings by cohort" style={{ width: axis.width + COHORT_LABEL_WIDTH }}>
          <colgroup><col style={{ width: COHORT_LABEL_WIDTH }} /><col style={{ width: axis.width }} /></colgroup>
          <thead><tr>
            <th scope="col" className={styles.cohortHeading}>Cohort</th>
            <th scope="col" className={styles.axis} aria-label="Module dates by month">
              {axis.months.map(month => <span key={month.label} className={styles.month}
                style={{ left: `${month.left}%`, width: `${month.width}%` }}>{month.label}</span>)}
            </th>
          </tr></thead>
          <tbody>{rows.map(row => {
            const { blocks } = cohortLanes(row, axis);
            return <tr key={row.id} data-testid={`cohort-${row.id}`}>
              <th scope="row" className={`${styles.cohortLabel} ${row.home ? styles.homeLabel : ''}`}>
                {row.name}{row.home && <span className={styles.homeBadge}> (Home cohort)</span>}
              </th>
              <td className={styles.track}>
                {axis.months.map(month => <span key={month.label} aria-hidden="true" className={styles.gridline} style={{ left: `${month.left}%` }} />)}
                <div className={styles.lanes}>
                {blocks.map(({ module, offering, lane, start, width, labelLeft, labelWidth, left, widthIncludingLabel }) =>
                  <article key={offering.id} className={`${styles.offering} ${row.home ? styles.home : styles.alternative}`}
                    data-testid={`offering-${offering.id}`} data-lane={lane}
                    aria-label={`${module.name}, ${row.name}, ${dateLabel(offering.start)} to ${dateLabel(offering.end)}${offering.groupName ? `, ${offering.groupName}` : ''}${row.home ? '' : ', Alternative option'}`}
                    style={{ gridArea: `${lane + 1} / 1`, marginLeft: left, width: widthIncludingLabel }}>
                    <div aria-hidden="true" className={styles.dateBar} style={{ left: start - left, width }} />
                    <div className={styles.card} style={{ left: labelLeft - left, width: labelWidth }}>
                      <h4 className={styles.moduleName} title={module.name}>{module.name}</h4>
                      <p className={styles.dates}><time dateTime={offering.start}>{dateLabel(offering.start)}</time> → <time dateTime={offering.end}>{dateLabel(offering.end)}</time></p>
                      {offering.groupName && <p className={styles.group} title={offering.groupName}>{offering.groupName}</p>}
                      <div className={styles.notes}>
                        {!row.home && <span className={styles.alternativeBadge}>Alternative option</span>}
                        {offering.extendsWindow && <span>Extends beyond the home-cohort window</span>}
                      </div>
                    </div>
                  </article>)}
                </div>
              </td>
            </tr>;
          })}</tbody>
        </table>
      </div>}
  </section>;
}
