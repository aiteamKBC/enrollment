import DOMPurify from 'dompurify';
import type { ImportedReviewSection as HistorySection } from '@/api/reviewHistory';
import { ImportedProgressCards } from './ImportedProgressCards';
import { adaptHistorySection, calendarLabel, decodeStructured, displayLabel, isEmpty, isTechnical, keyOf, normalizeImportedProgressData, record, type PresentationField, type PresentationSection } from './presentation';
import styles from './importedReview.module.css';

const htmlPattern = /<\/?[a-z][^>]*>/i;
const text = (value: unknown) => typeof value === 'string' ? value : '';

export function ImportedRichText({ value }: { value: string }) {
  const html = DOMPurify.sanitize(value, {
    ALLOWED_TAGS: ['p', 'br', 'ul', 'ol', 'li', 'strong', 'b', 'em', 'i', 'u', 's', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'a', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'span', 'div'],
    ALLOWED_ATTR: ['href', 'title', 'colspan', 'rowspan'],
    ALLOW_DATA_ATTR: false, ALLOW_ARIA_ATTR: false,
  });
  return <div className={styles.richText} dangerouslySetInnerHTML={{ __html: html }} />;
}

/** Read-only semantic fallback, also used for unknown historical structures. */
export function ImportedValue({ value, label = '', fieldType, depth = 0 }: { value: unknown; label?: string; fieldType?: string; depth?: number }) {
  if (depth > 12) return <span className={styles.note}>Further detail could not be displayed.</span>;
  const decoded = decodeStructured(value);
  if (decoded.malformed) return <span className={styles.note}>These details could not be displayed.</span>;
  value = decoded.value;
  if (isEmpty(value)) return <span className={styles.note}>Not provided</span>;
  const object = record(value);
  if (object && Array.isArray(object.characteristics) && record(object.competency)) return <SkillsRadar value={object} />;
  if (Array.isArray(value)) return <ul className={styles.valueList}>{value.map((item, i) => <li key={i}><ImportedValue value={item} depth={depth + 1} /></li>)}</ul>;
  if (object) {
    const entries = Object.entries(object).filter(([key, v]) => !isTechnical(key) && !isEmpty(v));
    return entries.length ? <dl className={styles.structured}>{entries.map(([key, v]) => <div key={key}><dt>{displayLabel(key)}</dt><dd><ImportedValue value={v} label={key} depth={depth + 1} /></dd></div>)}</dl> : <span className={styles.note}>No additional details recorded.</span>;
  }
  const boolean = typeof value === 'boolean' ? value : typeof value === 'string' && /^(yes|no|true|false)$/i.test(value.trim()) ? /^(yes|true)$/i.test(value.trim()) : fieldType === 'boolean' && [0, 1, '0', '1'].includes(value as number) ? Number(value) === 1 : null;
  if (boolean !== null) return <span className={styles.badge}>{boolean ? 'Yes' : 'No'}</span>;
  if (typeof value === 'string') {
    const formatted = calendarLabel(value, /time/i.test(label));
    if (formatted) return <span>{formatted}</span>;
    if (htmlPattern.test(value)) return <ImportedRichText value={value} />;
    return <span className={styles.plainText}>{value}</span>;
  }
  return <span>{String(value)}</span>;
}

function SkillsRadar({ value }: { value: Record<string, unknown> }) {
  const competency = record(value.competency) || {};
  const characteristics = (value.characteristics as unknown[]).map(record).filter((v): v is Record<string, unknown> => v !== null);
  return <div className={styles.skills}>
    <p className={styles.standardTitle}>{text(competency.name)}</p>
    <p className={styles.note}>Recorded assessments and their original level descriptions.</p>
    {characteristics.map((item, index) => {
      const level = record(item.assessedLevel);
      return <article key={index} className={styles.skill}><h4>{text(item.name) || `Characteristic ${index + 1}`}</h4>
        <div className={styles.skillAssessment}><span className={styles.badge}>{level?.level != null ? `Level ${String(level.level)}` : 'Not assessed'}</span>{!isEmpty(level?.shortDescription || level?.description) && <ImportedValue value={level?.shortDescription || level?.description} />}</div>
        {['notes', 'actions', 'assessments'].filter(key => !isEmpty(item[key])).map(key => <div key={key}><h5>{displayLabel(key)}</h5><ImportedValue value={item[key]} /></div>)}
        {Array.isArray(item.levels) && item.levels.length > 0 && <details><summary>Assessment scale</summary><ImportedValue value={item.levels} /></details>}
      </article>;
    })}
  </div>;
}

function safeHref(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined;
  try { const url = new URL(value); return ['https:', 'http:'].includes(url.protocol) ? url.href : undefined; } catch { return undefined; }
}

function Field({ field }: { field: PresentationField }) {
  return <div className={styles.question}>
    {field.label && <dt>{displayLabel(field.label)}</dt>}
    <dd>{!(field.fieldType === 'title_description' && isEmpty(field.value)) && <ImportedValue value={field.value} label={field.label} fieldType={field.fieldType} />}
      {field.description && <div className={styles.note}><ImportedValue value={field.description} /></div>}
      {field.links?.map((link, index) => { const href = safeHref(link.azure_url || link.url || link.href); return href ? <a className={styles.link} key={index} href={href} target="_blank" rel="noopener noreferrer">{link.text || link.title || `Attachment ${index + 1}`}</a> : null; })}
    </dd>
  </div>;
}

function visibleFields(fields: PresentationField[]) {
  return fields.filter(field => !isTechnical(field.label || '') && (!isEmpty(field.value) || field.required || field.preserveEmpty || field.links?.length || field.description));
}

export function ImportedSectionContent({ section }: { section: PresentationSection }) {
  const fields = visibleFields(section.fields);
  const info = keyOf(section.name.replace(/\s+(completed|incomplete)$/i, '')) === 'learnerinformation';
  const identity = info ? fields.find(field => ['name', 'learnername'].includes(keyOf(field.label || ''))) : undefined;
  const name = text(identity?.value);
  return <div className={styles.content}>
    <div className={info && identity ? styles.learnerInfo : undefined}>
      {identity && <div className={styles.identity}><span className={styles.avatar} aria-hidden="true">{name.split(/\s+/).filter(Boolean).slice(0, 2).map(part => part[0]).join('').toUpperCase()}</span><h3>{name}</h3><p>Learner information</p></div>}
      <dl className={info ? styles.infoGrid : styles.questions}>
        {fields.filter(field => field !== identity).map((field, index) => keyOf(field.label || '') === 'progress' && /learning\s*progress/i.test(section.name)
          ? <div key={index} className={styles.progressField}><dt className={styles.srOnly}>Learning progress</dt><dd><ImportedProgressCards historical={section.historicalPresentation} data={normalizeImportedProgressData(field.value)} renderUnknown={value => <ImportedValue value={value} />} /></dd></div>
          : <Field key={index} field={field} />)}
      </dl>
    </div>
    {section.tables.map((rows, i) => rows.length > 0 && <div key={i} className={styles.tableWrap}><table aria-label={section.name}><tbody>{rows.map((row, r) => <tr key={r}>{(Array.isArray(row) ? row : [row]).map((cell, c) => <td key={c}><ImportedValue value={cell} /></td>)}</tr>)}</tbody></table></div>)}
    {/* rawText is the export's duplicate of fields/tables. Use it only for text-only exports. */}
    {(section.preserveRawText || (!section.fields.length && !section.tables.length)) && !isEmpty(section.rawText) && <ImportedValue value={section.rawText} />}
    {!fields.length && !section.tables.some(table => table.length) && (section.fields.length > 0 || isEmpty(section.rawText)) && <p className={styles.note}>No response was recorded for this section.</p>}
  </div>;
}

export function ImportedReviewSection({ section }: { section: HistorySection }) {
  return <ImportedSectionContent section={adaptHistorySection(section)} />;
}
