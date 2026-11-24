import { useEffect, useLayoutEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { ArrowDown, ArrowUp, ChevronDown, Plus } from 'lucide-react';
import { btnPrimary, btnSecondary } from '@/pages/users/components/ui';
import type { CatalogRecord, Cohort, Delivery, Detail, PinnedCurriculum, Resource, StaffOptions, Topic } from './catalogApi';
import { calculatePricing, cohortDates, defaultLectureHours, formatAmountInput, gbp, inclusiveDays, moduleEnd, monthCount, savedDeliveryEndOverride, showDate, showDateRange, showTime } from './catalogMath';
import { catalogueFocus, catalogueInput, UnitInput } from './catalogControls';
import { CatalogParents, hasValidParents, type CatalogParentsProps } from './CatalogParents';
import { CatalogStaffSelect, type StaffDirectory } from './CatalogStaffSelect';
import { CatalogTimeInput } from './CatalogTimeInput';
import { CurriculumContent } from './CatalogCurriculumContent';
import { CatalogKsbPicker } from './CatalogKsbPicker';
import type { CatalogValues } from './catalogValidation';

export const weekdays = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
export type EditorState = { resource: Resource | 'programme'; record?: CatalogRecord; parentId?: string };
type Choice = { value: string; label: string; moduleId?: string; weeks?: number };
type Field = {
  name: string; label: string; defaultValue?: string; optional?: boolean; nullable?: boolean;
  type?: 'number' | 'text' | 'textarea' | 'date' | 'time'; options?: Choice[];
  min?: number; max?: number; step?: string; maxLength?: number; help?: string;
  unit?: 'GBP' | 'months';
  staffRole?: keyof StaffOptions;
};
const named = { name: 'name', label: 'Name', maxLength: 200 };
const description: Field = { name: 'description', label: 'Description', type: 'textarea', maxLength: 10000 };
const integer = (name: string, label: string, min: number, max: number, optional = false): Field => (
  { name, label, type: 'number', min, max, optional, nullable: optional }
);
const money = (name: string, label: string, optional = false): Field => (
  { name, label, type: 'number', min: 0, max: 9_999_999_999.99, step: '0.01', optional, nullable: optional, unit: 'GBP' }
);
const start: Field = { name: 'start_date', label: 'Start date', type: 'date' };

function moduleChoices(detail: Detail | null, previous: Record<string, unknown>): Choice[] {
  const options = (detail?.modules ?? []).flatMap(module =>
    module.archived_at || !module.current_revision ? [] : [{
      value: module.current_revision.id, moduleId: module.id, weeks: module.weeks,
      label: `${module.name} · v${module.current_revision.number} (latest)`,
    }],
  );
  const pinned = previous.curriculum as PinnedCurriculum | undefined;
  if (pinned && typeof previous.module_id === 'string' && !options.some(option => option.value === pinned.id)) {
    options.unshift({ value: pinned.id, moduleId: previous.module_id, weeks: pinned.weeks, label: `${pinned.name} · v${pinned.number} (currently scheduled)` });
  }
  return options;
}

function fieldsFor(resource: EditorState['resource'], detail: Detail | null, previous: Record<string, unknown>, preview: boolean): Field[] {
  switch (resource) {
    case 'programme': return [
      { ...named, label: 'Programme name' }, { name: 'code', label: 'Programme code', maxLength: 80 },
      { name: 'level', label: 'Level', options: Array.from({ length: 8 }, (_, index) => ({ value: String(index + 1), label: `Level ${index + 1}` })) },
      { name: 'status', label: 'Status', defaultValue: 'draft', options: [{ value: 'draft', label: 'Draft' }, { value: 'active', label: 'Active' }] },
      { ...description, optional: true },
      { name: 'epao', label: 'EPA organisation', optional: true, maxLength: 200, help: 'Enter the full name of the end-point assessment organisation. Leave blank if it has not been confirmed.' },
      money('total_cost', 'Total per apprentice (GBP)'), money('epa_cost', 'EPA cost (GBP)'),
      money('funding_band_maximum', 'Funding band maximum (GBP)', true),
      { name: 'lecture_hours', label: preview ? 'Hours per session' : 'Hours per lecture', type: 'number', min: 0.01, max: 24, step: '0.01', defaultValue: defaultLectureHours,
        help: `Used with the selected group’s ${preview ? 'session' : 'lecture'} count to calculate training hours.` },
    ];
    case 'cohorts': return [
      { ...named, label: 'Cohort name' }, start,
      { ...integer('training_duration_months', 'Planned duration (months)', 1, 120), unit: 'months' },
      { ...integer('epa_period', 'EPA period (months)', 0, 3650), unit: 'months', help: 'Enter 0 only if no additional EPA time is planned.' },
    ];
    case 'groups': return [
      { ...named, label: 'Group name' },
      { name: 'coach_id', label: 'Skills Coach', optional: true, nullable: true, staffRole: 'coaches' },
      { name: 'weekday', label: 'Day of week', options: weekdays.map((label, index) => ({ value: String(index + 1), label })) },
      { name: 'start_time', label: 'Start time', type: 'time' },
      { name: 'end_time', label: 'End time', type: 'time' },
    ];
    case 'modules': return [
      { ...named, label: 'Module name' },
      ...(preview ? [{ ...integer('weeks', 'Number of sessions', 1, 520), help: 'One session per week. Add up to one weekly topic per session.' }]
        : [integer('weeks', 'Number of weeks', 1, 520), integer('lectures', 'Number of lectures', 0, 10000, true)]),
      integer('reviews', preview ? 'Progress reviews' : 'Number of reviews', 0, 10000, true),
      integer('books', 'Number of books', 0, 10000, true),
    ];
    case 'deliveries': return [
      { name: 'module_revision_id', label: 'Module and revision',
        defaultValue: (previous.curriculum as PinnedCurriculum | undefined)?.id, options: moduleChoices(detail, previous) },
      start, { name: 'end_date', label: 'End date', type: 'date' },
      { name: 'tutor_id', label: 'Tutor', optional: Boolean(previous.id && !previous.tutor_id), staffRole: 'tutors' },
    ];
    case 'ksbs': return [{ name: 'code', label: 'KSB code', maxLength: 40 }, { ...description, maxLength: 5000 }];
  }
}

export const recordLabels = {
  programme: 'Programme', cohorts: 'Cohort', groups: 'Group', modules: 'Module template', deliveries: 'Scheduled module', ksbs: 'KSB',
};
const editorLabel = (resource: EditorState['resource']) => resource === 'ksbs' ? 'KSB' : recordLabels[resource].toLowerCase();
export const editorTitle = (resource: EditorState['resource'], editing: boolean) =>
  resource === 'deliveries' && !editing ? 'Add module' : `${editing ? 'Edit' : 'Add'} ${editorLabel(resource)}`;

function fieldSections(resource: EditorState['resource'], fields: Field[]): [string, Field[]][] {
  if (resource === 'programme') return [
    ['Basic information', fields.slice(0, 5)], ['Programme delivery', fields.slice(5, 6)], ['Financial information', fields.slice(6)],
  ];
  if (resource === 'cohorts') return [['Training schedule', fields.slice(0, 3)], ['End-point assessment', fields.slice(3)]];
  if (resource === 'modules') return [['Module information', fields.slice(0, 2)], ['Delivery counts', fields.slice(2)]];
  if (resource === 'groups') return [['Group details', fields.slice(0, 2)], ['Weekly schedule', fields.slice(2)]];
  return [[resource === 'deliveries' ? 'Delivery and curriculum revision' : 'KSB details', fields]];
}

function FormSection({ title, children, headingId }: { title: string; children: ReactNode; headingId?: string }) {
  return <section className="min-w-0 space-y-4 rounded-xl border border-background-200 bg-background-50 p-4 sm:p-5">
    <h3 id={headingId} tabIndex={headingId ? -1 : undefined} className="scroll-mt-8 text-base font-semibold text-foreground-900">{title}</h3>
    {children}
  </section>;
}

function ScheduleFacts({ rows }: { rows: [string, ReactNode][] }) {
  return <dl className="grid grid-cols-2 gap-3 rounded-lg bg-background-100 p-3 text-sm" aria-live="polite">
    {rows.map(([label, value]) => <div key={label}>
      <dt className="text-foreground-600">{label}</dt>
      <dd className="mt-0.5 font-semibold tabular-nums">{value}</dd>
    </div>)}
  </dl>;
}

function CalculatedEndInput({ id, label, expected, override, onChange, orderError, expectedBasis, required = false }: {
  id: string; label: string; expected: string | null; override: string | null; onChange: (value: string | null) => void;
  orderError: string | false | null; expectedBasis: string;
  required?: boolean;
}) {
  const manual = override !== null;
  const difference = Boolean(override && expected && override !== expected);
  return <div className="space-y-1.5 text-sm">
    <label className="font-medium" htmlFor={id}>{label}</label>
    <input id={id} type="date" className={catalogueInput} value={override ?? expected ?? ''}
      max="9999-12-31" required={required || manual} aria-invalid={Boolean(orderError)} aria-describedby={`${id}-status`}
      onChange={event => onChange(event.currentTarget.value)} />
    <div id={`${id}-status`} className="space-y-1" aria-live="polite">
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <span className="text-xs font-medium text-foreground-700">{manual ? 'Manually overridden' : 'Auto-calculated'}</span>
        {manual && <button type="button" className={`rounded text-xs font-semibold text-primary-700 hover:underline ${catalogueFocus}`}
          aria-label={`Reset ${label.toLowerCase()} to automatic`} onClick={() => {
            onChange(null);
            document.getElementById(id)?.focus();
          }}>Reset to automatic</button>}
      </div>
      {orderError && <p className="text-xs text-red-800">{orderError}</p>}
      {difference && <p className="text-xs text-amber-900 dark:text-amber-200">{expectedBasis} gives {showDate(expected!)}.</p>}
    </div>
  </div>;
}

export function CatalogEditor({ editor, detail, staff, staffLoading, staffError, retryStaff, addPreviewStaff, busy, error, preview = false, parents, onSave, onCancel, onReload, onDirtyChange, onClearError }: {
  editor: EditorState; detail: Detail | null; staff: StaffOptions | null; busy: boolean; error: string; preview?: boolean;
  staffLoading?: boolean; staffError?: string | null; retryStaff?: () => void; addPreviewStaff?: StaffDirectory['addPreviewStaff'];
  parents?: CatalogParentsProps;
  onSave: (data: CatalogValues) => void; onCancel: () => void; onReload?: () => void;
  onDirtyChange?: (dirty: boolean) => void;
  onClearError?: () => void;
}) {
  const previous = (editor.record ?? {}) as Record<string, unknown>;
  const fields = fieldsFor(editor.resource, detail, previous, preview);
  const isDelivery = editor.resource === 'deliveries';
  const savedDelivery = isDelivery ? editor.record as Delivery | undefined : undefined;
  const canCreateTemplate = isDelivery && !savedDelivery;
  const templateFields = canCreateTemplate ? fieldsFor('modules', detail, {}, true) : [];
  const [templateMode, setTemplateMode] = useState<'existing' | 'new'>();
  const [initialDeliveryOverride] = useState(() => savedDeliveryEndOverride(savedDelivery));
  const [manualDeliveryEnd, setManualDeliveryEnd] = useState(initialDeliveryOverride);
  const [initialTopics] = useState(() => ((previous.weekly_topics as Topic[] | undefined) ?? [])
    .map(topic => ({ ...topic, localKey: topic.id ?? crypto.randomUUID() })));
  const [topics, setTopics] = useState(initialTopics);
  const [openTopic, setOpenTopic] = useState(initialTopics[0]?.localKey);
  const [topicNotice, setTopicNotice] = useState('');
  const [initialDraft] = useState<Record<string, string>>(() => Object.fromEntries([...fields, ...templateFields].map(field => {
    const value = String(previous[field.name] ?? field.defaultValue ?? '');
    if (editor.resource === 'cohorts' && field.name === 'epa_period' && previous.epa_unit && previous.epa_unit !== 'months') {
      return [field.name, ''];
    }
    return [field.name, field.unit === 'GBP' ? formatAmountInput(value, preview) : field.type === 'time' ? showTime(value) : value];
  })));
  const [draft, setDraft] = useState(initialDraft);
  const [initialKsbIds] = useState(() => (previous.ksb_ids as string[] | undefined) ?? []);
  const [selectedKsbs, setSelectedKsbs] = useState(initialKsbIds);
  const [initialParents] = useState(parents?.selection);
  const errorSummary = useRef<HTMLDivElement>(null);
  const title = editorTitle(editor.resource, Boolean(editor.record));
  const isCohortEditor = editor.resource === 'cohorts';
  const savedCohort = isCohortEditor ? editor.record as Cohort | undefined : undefined;
  const [trainingOverride, setTrainingOverride] = useState(savedCohort?.training_end_override ?? null);
  const [apprenticeshipOverride, setApprenticeshipOverride] = useState(savedCohort?.apprenticeship_end_override ?? null);
  const trainingMonths = draft.training_duration_months ? Number(draft.training_duration_months) : null;
  const legacyScheduleLocked = Boolean(savedCohort && savedCohort.training_duration_months == null && trainingMonths == null);
  const cohortStart = legacyScheduleLocked ? savedCohort!.start_date : draft.start_date;
  const epaPeriod = legacyScheduleLocked ? savedCohort!.epa_period : draft.epa_period ? Number(draft.epa_period) : null;
  const dates = isCohortEditor ? cohortDates(cohortStart, trainingMonths ?? null, epaPeriod, {
    trainingOverride, apprenticeshipOverride,
    legacy: legacyScheduleLocked ? {
      trainingEnd: savedCohort!.automatic_training_end_date ?? savedCohort!.training_end_date, epaUnit: savedCohort!.epa_unit,
    } : undefined,
  }) : null;
  const totalDays = dates?.apprenticeshipEnd ? inclusiveDays(cohortStart, dates.apprenticeshipEnd) : null;
  const totalDuration = totalDays == null ? 'Not calculated'
    : legacyScheduleLocked || trainingOverride || apprenticeshipOverride || trainingMonths == null || epaPeriod == null
      ? `${totalDays} days` : monthCount(trainingMonths + epaPeriod);
  const quote = editor.resource === 'programme' ? calculatePricing(
    { total_cost: draft.total_cost, epa_cost: draft.epa_cost, lecture_hours: draft.lecture_hours },
    [], preview,
  ) : null;
  const revisionField = fields.find(field => field.name === 'module_revision_id');
  const selectedModule = revisionField?.options?.find(option => option.value === draft.module_revision_id);
  const selectedPin = savedDelivery && savedDelivery.curriculum.id === selectedModule?.value ? savedDelivery.curriculum : undefined;
  const selectedTemplate = selectedModule && detail?.modules.find(row => row.current_revision?.id === selectedModule.value);
  const selectedCurriculum = selectedPin ?? selectedTemplate;
  const selectedRevision = selectedPin?.number ?? selectedTemplate?.current_revision?.number;
  const coveredKsbs = selectedPin?.ksbs ?? detail?.ksbs.filter(ksb => selectedTemplate?.ksb_ids.includes(ksb.id)) ?? [];
  const parentsReady = !parents || (!parents.loading && !parents.error && hasValidParents(parents.resource, detail, parents.selection));
  const creatingTemplate = canCreateTemplate && (templateMode === 'new' || (!templateMode && parentsReady && !revisionField?.options?.length));
  const editingTemplate = editor.resource === 'modules' || creatingTemplate;
  const moduleRequired = isDelivery && !selectedModule && !creatingTemplate;
  const tutorRequired = canCreateTemplate || Boolean(savedDelivery?.tutor_id);
  const suggestedEnd = isDelivery ? moduleEnd(draft.start_date, creatingTemplate ? Number(draft.weeks) : selectedModule?.weeks ?? 0) : null;
  const endDate = manualDeliveryEnd ?? suggestedEnd ?? '';
  const group = detail?.groups.find(row => row.id === (savedDelivery?.group_id ?? editor.parentId));
  const cohort = detail?.cohorts.find(row => row.id === (group?.cohort_id ?? parents?.selection.cohortId ?? editor.parentId));
  const deliveryDateError = draft.start_date && endDate
    ? endDate < draft.start_date ? 'End date must be on or after the start date.'
      : cohort && (draft.start_date < cohort.start_date || endDate > cohort.training_end_date)
        ? 'Both dates must fit within the cohort training dates.' : null
    : null;
  const weekLimit = Number(draft.weeks) || 0;
  const dirty = (isDelivery && manualDeliveryEnd !== initialDeliveryOverride) ||
    trainingOverride !== (savedCohort?.training_end_override ?? null) ||
    apprenticeshipOverride !== (savedCohort?.apprenticeship_end_override ?? null) || (parents && initialParents && (
    parents.selection.programmeId !== initialParents.programmeId || parents.selection.cohortId !== initialParents.cohortId ||
    parents.selection.groupId !== initialParents.groupId
  )) || Object.keys(initialDraft).some(key => draft[key] !== initialDraft[key])
    || selectedKsbs.length !== initialKsbIds.length || selectedKsbs.some(id => !initialKsbIds.includes(id))
    || topics.length !== initialTopics.length || topics.some((topic, index) =>
      topic.week !== initialTopics[index]?.week || topic.content !== initialTopics[index]?.content);
  const changeField = (name: string, value: string) => setDraft(current => ({ ...current, [name]: value }));

  useEffect(() => {
    if (error) errorSummary.current?.focus();
  }, [error]);

  useLayoutEffect(() => { onDirtyChange?.(Boolean(dirty)); }, [dirty, onDirtyChange]);

  function moveTopic(index: number, direction: number) {
    const other = index + direction;
    if (!topics[other]) return;
    const moved = topics[index];
    setTopicNotice(`Topic moved to week ${topics[other].week}.`);
    setTopics(rows => {
      return rows.map((row, position) => {
        if (position === index) return { ...rows[other], week: row.week };
        if (position === other) return { ...rows[index], week: row.week };
        return row;
      });
    });
    setOpenTopic(moved.localKey);
    requestAnimationFrame(() => document.getElementById(`topic-summary-${moved.localKey}`)?.focus());
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (onReload || busy || !parentsReady || moduleRequired || (tutorRequired && !draft.tutor_id)) return;
    const data: Record<string, unknown> = {};
    for (const field of fields) {
      if (legacyScheduleLocked && field.name !== 'name') continue;
      if (creatingTemplate && field.name === 'module_revision_id') continue;
      if (isDelivery && field.name === 'end_date') continue;
      const value = String(draft[field.name] ?? '').trim();
      if (editor.record && (field.staffRole || field.type === 'time' || field.name === 'training_duration_months') &&
        value === initialDraft[field.name]) continue;
      data[field.name] = value || (field.nullable ? null : '');
    }
    if (trainingOverride !== (savedCohort?.training_end_override ?? null)) data.training_end_override = trainingOverride;
    if (apprenticeshipOverride !== (savedCohort?.apprenticeship_end_override ?? null)) data.apprenticeship_end_override = apprenticeshipOverride;
    if (editor.resource === 'groups' && !editor.record) data.cohort_id = parents?.selection.cohortId ?? editor.parentId;
    if (editor.resource === 'deliveries' && !editor.record) {
      data.group_id = parents?.selection.groupId ?? editor.parentId;
      if (parents) data.cohort_id = parents.selection.cohortId;
    }
    if (isDelivery) {
      if (!savedDelivery || manualDeliveryEnd !== initialDeliveryOverride) data.end_date = manualDeliveryEnd;
      if (!creatingTemplate) data.module_id = selectedModule?.moduleId;
    }
    if (editingTemplate) {
      const template = creatingTemplate
        ? Object.fromEntries(templateFields.map(field => [field.name, draft[field.name].trim() || (field.nullable ? null : '')]))
        : data;
      if (preview || creatingTemplate) template.lectures = template.weeks;
      template.weekly_topics = topics.map(({ week, content }) => ({ week, content }));
      template.ksb_ids = selectedKsbs;
      if (creatingTemplate) data.new_template = template;
    }
    onSave(data);
  }

  function renderFields(entries: Field[], weeklySchedule = false) {
    return <div className={`grid gap-4 sm:grid-cols-2 ${weeklySchedule ? 'lg:grid-cols-3' : ''}`}>
          {entries.filter(field => !legacyScheduleLocked || (field.name !== 'start_date' && field.name !== 'epa_period')).map(field => {
          const control = `catalog-field-${field.name}`;
          if (isDelivery && field.name === 'end_date') {
            return <CalculatedEndInput key={field.name} id={control} label={field.label} expected={suggestedEnd}
              override={manualDeliveryEnd} orderError={deliveryDateError} expectedBasis="The module schedule" required
              onChange={value => {
                if (error && !onReload) onClearError?.();
                setManualDeliveryEnd(value);
              }} />;
          }
          const value = draft[field.name];
          const help = field.type === 'time' ? 'group-time-help' : field.help ? `${control}-help` : undefined;
          const legacyDuration = legacyScheduleLocked && field.name === 'training_duration_months';
          const input = { id: control, name: field.name, 'aria-describedby': legacyDuration ? 'legacy-cohort-duration-help' : help,
            required: !field.optional && !legacyDuration };
          const width = field.name === 'weekday' ? 'sm:col-span-2 sm:max-w-sm lg:col-span-1'
            : field.type === 'textarea' ? 'sm:col-span-2' : '';
          return <div key={field.name} className={`min-w-0 space-y-1.5 text-sm ${width}`}>
            <label htmlFor={control} className="font-medium">{field.label}</label>
            {field.optional && <span className="ml-2 text-xs text-foreground-600">Optional</span>}
            {field.staffRole ? (
              <CatalogStaffSelect id={control} label={field.label} role={field.staffRole}
                directory={{ data: staff, loading: staffLoading, error: staffError, retry: retryStaff, addPreviewStaff }}
                value={value} required={!field.optional} onChange={next => changeField(field.name, next)} />
            ) : field.options ? (
              <select {...input} className={catalogueInput} value={value} onChange={event => changeField(field.name, event.currentTarget.value)}
                disabled={field.name === 'module_revision_id' && !field.options.length}>
                <option value="">{field.optional ? 'Unassigned'
                  : field.name === 'module_revision_id' && !field.options.length ? 'No templates available' : 'Select an option'}</option>
                {field.options.map(choice => <option key={choice.value} value={choice.value}>{choice.label}</option>)}
              </select>
            ) : field.type === 'textarea' ? (
              <textarea {...input} className={catalogueInput} value={value} maxLength={field.maxLength} rows={3} onChange={event => changeField(field.name, event.currentTarget.value)} />
            ) : field.unit ? (
              <UnitInput {...input} unit={field.unit} value={value} onValue={next => changeField(field.name, next)}
                wholePounds={preview && field.unit === 'GBP'} min={field.min} max={field.max} step={field.step} />
            ) : field.type === 'time' ? (
              <CatalogTimeInput {...input} label={field.label} value={value} onValue={next => changeField(field.name, next)} />
            ) : (
              <input {...input} className={catalogueInput} type={field.type ?? 'text'} value={value}
                onChange={event => {
                  const next = event.currentTarget.value;
                  if (isDelivery && field.type === 'date' && error && !onReload) onClearError?.();
                  changeField(field.name, next);
                }} spellCheck={field.name === 'code' ? false : undefined}
                min={field.min} max={field.type === 'date' ? '9999-12-31' : field.max} step={field.step} maxLength={field.maxLength} />
            )}
            {field.help && <p id={help} className="text-xs text-foreground-600">{field.help}</p>}
          </div>;
        })}
    </div>;
  }

  return (
    <form id="catalog-editor" onSubmit={submit} autoComplete="off"
      className="mx-auto max-w-5xl space-y-4 sm:[&_fieldset_:is(input,select,textarea,button,summary)]:scroll-mb-24" aria-label={title}>
      <h2 className="text-xl font-semibold text-foreground-900">{title}</h2>
      {editor.resource !== 'programme' && !isDelivery && detail && !parents && <p className="text-sm text-foreground-600">
        {[detail.programme.name, cohort?.name, group?.name].filter(Boolean).join(' / ')}
      </p>}
      {editor.resource === 'modules' && <p className="text-sm text-foreground-600">
        This reusable template belongs to the programme. Scheduled modules keep their pinned revision.
      </p>}
      <p className="text-sm text-foreground-600">Fields are required unless marked optional.</p>
      {error && <div ref={errorSummary} role="alert" tabIndex={-1} className={`rounded-lg bg-red-50 p-3 text-sm text-red-800 ${catalogueFocus}`}>
        <p className="font-semibold">Check the form before continuing</p><p className="mt-1 whitespace-pre-line">{error}</p>
      </div>}
      <fieldset disabled={busy} className="min-w-0 space-y-4">
        {parents && <FormSection title="Parent records">
          <CatalogParents {...parents} onChange={selection => {
            if (isDelivery) {
              const programmeChanged = selection.programmeId !== parents.selection.programmeId;
              if (programmeChanged) {
                changeField('module_revision_id', '');
                setSelectedKsbs([]);
              }
              if (programmeChanged || selection.cohortId !== parents.selection.cohortId) {
                changeField('start_date', '');
                setManualDeliveryEnd(null);
              }
              if (selection.groupId && detail?.deliveries.some(row => !row.archived_at &&
                row.group_id === selection.groupId && row.module_id === selectedModule?.moduleId)) {
                changeField('module_revision_id', '');
              }
            }
            parents.onChange(selection);
          }} />
        </FormSection>}
        {savedDelivery && detail && <FormSection title="Parent records">
          <ScheduleFacts rows={[
            ['Programme', detail.programme.name], ['Cohort', cohort?.name ?? 'Unavailable'], ['Group', group?.name ?? 'Unavailable'],
          ]} />
        </FormSection>}
        {isDelivery && revisionField && <FormSection title="Module">
          {canCreateTemplate && <fieldset className="flex flex-wrap gap-x-5 gap-y-2 text-sm">
            <legend className="sr-only">How to add the module</legend>
            <label className="flex min-h-10 cursor-pointer items-center gap-2">
              <input type="radio" name="template-choice" className="accent-primary-600"
                checked={!creatingTemplate} disabled={!parentsReady} onChange={() => setTemplateMode('existing')} />
              Select existing module
            </label>
            <label className="flex min-h-10 cursor-pointer items-center gap-2">
              <input type="radio" name="template-choice" className="accent-primary-600"
                checked={creatingTemplate} disabled={!parentsReady} onChange={() => setTemplateMode('new')} />
              Create new module
            </label>
          </fieldset>}
          {creatingTemplate ? <p className="text-sm text-foreground-600">Saving adds this module to the library and schedules it for the selected group.</p>
            : renderFields([revisionField])}
          {!creatingTemplate && selectedCurriculum && <section aria-label="Selected module curriculum" className="space-y-3">
            <ScheduleFacts rows={[
              ['Revision', `v${selectedRevision}`], ['Sessions', selectedCurriculum.lectures ?? 'Not set'],
              ['Progress reviews', selectedCurriculum.reviews ?? 'Not set'], ['Books', selectedCurriculum.books ?? 'Not set'],
            ]} />
            <details key={selectedModule?.value} className="space-y-3">
              <summary className={`cursor-pointer text-sm font-semibold text-primary-700 ${catalogueFocus}`}>View KSBs and weekly topics</summary>
              <CurriculumContent topics={selectedCurriculum.weekly_topics} ksbs={coveredKsbs} embedded />
            </details>
          </section>}
          {!creatingTemplate && canCreateTemplate && parentsReady && !revisionField.options?.length &&
            <p className="text-sm text-foreground-600">No modules are available in this programme. Choose Create new module to continue.</p>}
        </FormSection>}
        {(isDelivery ? creatingTemplate ? [['Module details', templateFields] as [string, Field[]]] : []
          : fieldSections(editor.resource, fields)).map(([section, entries]) => <FormSection key={section} title={section}>
          {renderFields(entries, section === 'Weekly schedule')}
          {section === 'Weekly schedule' && <p id="group-time-help" className="text-xs text-foreground-600">Type a 24-hour time like 1111 or 11:11, or use the clock button.</p>}
          {section === 'Financial information' && preview && <p className="text-xs text-foreground-600">Enter all costs in whole pounds.</p>}
          {section === 'Financial information' && quote && <p className="mt-3 text-sm text-foreground-700">Base excluding EPA: <strong>{gbp(quote.base_cost, preview)}</strong></p>}
          {section === 'Training schedule' && <>
            {legacyScheduleLocked && <>
              <p id="legacy-cohort-duration-help" className="text-sm text-foreground-600">
                No planned duration was recorded. Enter one to recalculate automatic dates and edit the saved start date or EPA period.
              </p>
              <ScheduleFacts rows={[[ 'Saved start date', showDate(savedCohort!.start_date) ]]} />
            </>}
            {dates && <CalculatedEndInput id="cohort-training-end" label="Training end date" expected={dates.expectedTraining}
              override={trainingOverride} onChange={setTrainingOverride}
              orderError={dates.trainingEnd && cohortStart && dates.trainingEnd < cohortStart ? 'Training end date must be on or after the cohort start date.' : false}
              expectedBasis={legacyScheduleLocked ? 'The saved legacy schedule' : 'The cohort’s planned duration'} />}
          </>}
          {section === 'End-point assessment' && dates && <>
            <CalculatedEndInput id="cohort-apprenticeship-end" label="Apprenticeship end date" expected={dates.expectedApprenticeship}
              override={apprenticeshipOverride} onChange={setApprenticeshipOverride}
              orderError={dates.apprenticeshipEnd && dates.trainingEnd && dates.apprenticeshipEnd < dates.trainingEnd ? 'Apprenticeship end date must be on or after the training end date.' : false}
              expectedBasis="The scheduled EPA period" />
            <ScheduleFacts rows={[
              ...(legacyScheduleLocked ? [['Saved EPA period', `${savedCohort!.epa_period} ${savedCohort!.epa_unit}`] as [string, ReactNode]] : []),
              ['Total apprenticeship duration', totalDuration],
            ]} />
          </>}
        </FormSection>)}
        {editingTemplate && <>
          <FormSection title="Weekly topics">
            <p className="text-sm text-foreground-600">Open a week to edit its content. Move a topic up or down to swap its week with the adjacent topic.</p>
            <p role="status" className="sr-only">{topicNotice}</p>
            <div className="divide-y divide-background-200 border-y border-background-200">
              {topics.map((topic, index) => (
                <details key={topic.localKey} open={openTopic === topic.localKey}>
                  <summary id={`topic-summary-${topic.localKey}`} className={`cursor-pointer py-3 text-sm ${catalogueFocus}`}
                    onClick={event => { event.preventDefault(); setOpenTopic(current => current === topic.localKey ? undefined : topic.localKey); }}>
                    <strong>Week {topic.week || '—'}</strong>
                    {openTopic !== topic.localKey && <span className="ml-2 break-words text-foreground-600" aria-hidden>
                      {topic.content ? topic.content.length > 72 ? `${topic.content.slice(0, 72)}…` : topic.content : 'No topic text yet'}
                    </span>}
                  </summary>
                  <div className="grid gap-3 pb-4 sm:grid-cols-[7rem_minmax(0,1fr)]">
                    <label className="space-y-1 text-sm font-medium">Week<input className={catalogueInput} type="number"
                      name={`topic-week-${topic.localKey}`} min={1} max={weekLimit || 520} required value={topic.week || ''}
                      onInvalid={event => { event.currentTarget.closest('details')!.open = true; setOpenTopic(topic.localKey); }}
                      onChange={event => {
                        const week = Number(event.currentTarget.value);
                        setTopics(rows => rows.map(row => row.localKey === topic.localKey ? { ...row, week } : row));
                      }} /></label>
                    <div className="space-y-1 text-sm font-medium">
                      <label htmlFor={`topic-content-${topic.localKey}`}>Topic content</label>
                      <textarea id={`topic-content-${topic.localKey}`} className={catalogueInput} rows={2}
                        name={`topic-content-${topic.localKey}`} maxLength={5000} required value={topic.content}
                        onInvalid={event => { event.currentTarget.closest('details')!.open = true; setOpenTopic(topic.localKey); }}
                        onChange={event => {
                          const content = event.currentTarget.value;
                          setTopics(rows => rows.map(row => row.localKey === topic.localKey ? { ...row, content } : row));
                        }} />
                    </div>
                    <div className="flex flex-wrap gap-2 sm:col-span-2">
                      <button className={`${btnSecondary} ${catalogueFocus}`} type="button" disabled={index === 0}
                        aria-label={`Move up week ${topic.week}`} onClick={() => moveTopic(index, -1)}><ArrowUp size={14} aria-hidden /> Move up</button>
                      <button className={`${btnSecondary} ${catalogueFocus}`} type="button" disabled={index === topics.length - 1}
                        aria-label={`Move down week ${topic.week}`} onClick={() => moveTopic(index, 1)}><ArrowDown size={14} aria-hidden /> Move down</button>
                      <button className={`${btnSecondary} ${catalogueFocus}`} type="button"
                        aria-label={`Remove topic for week ${topic.week}`} onClick={() => {
                          setTopics(rows => rows.filter(row => row.localKey !== topic.localKey));
                          setTopicNotice(`Week ${topic.week} topic removed.`);
                          requestAnimationFrame(() => document.getElementById('catalog-add-topic')?.focus());
                        }}>Remove topic</button>
                    </div>
                  </div>
                </details>
              ))}
            </div>
            <button id="catalog-add-topic" className={`${btnSecondary} ${catalogueFocus}`} type="button"
              disabled={weekLimit < 1 || topics.length >= weekLimit} onClick={() => {
                const used = new Set(topics.map(topic => topic.week));
                const week = Array.from({ length: Math.min(520, weekLimit) }, (_, index) => index + 1).find(candidate => !used.has(candidate));
                if (!week) return;
                const localKey = crypto.randomUUID();
                setTopics(rows => [...rows, { localKey, week, content: '' }].sort((left, right) => left.week - right.week));
                setOpenTopic(localKey);
                setTopicNotice(`Week ${week} added.`);
                requestAnimationFrame(() => document.getElementById(`topic-summary-${localKey}`)?.focus());
              }}><Plus size={14} aria-hidden /> Add weekly topic</button>
            {!weekLimit && <p className="text-xs text-foreground-600">Set the number of {preview || creatingTemplate ? 'sessions' : 'weeks'} before adding topics.</p>}
          </FormSection>
          <FormSection title="KSB coverage">
            <CatalogKsbPicker key={detail?.programme.id} rows={detail?.ksbs ?? []} initialIds={initialKsbIds}
              selectedIds={selectedKsbs} onChange={setSelectedKsbs} />
            {creatingTemplate && <button type="button" className={`${btnSecondary} ${catalogueFocus}`} disabled={!parentsReady}
              onClick={() => document.getElementById('catalog-module-schedule')?.focus()}>
              Next to schedule and tutor <ChevronDown size={15} aria-hidden />
            </button>}
          </FormSection>
        </>}
        {isDelivery && <FormSection title="Schedule and tutor" headingId="catalog-module-schedule">
          {cohort && <p className="text-sm text-foreground-600">Cohort training dates {showDateRange(cohort.start_date, cohort.training_end_date)}.</p>}
          <fieldset disabled={!parentsReady} className="min-w-0">
            {renderFields(fields.filter(field => field !== revisionField))}
          </fieldset>
        </FormSection>}
      </fieldset>
      <footer className="flex flex-wrap items-center justify-end gap-2 rounded-t-lg border-t border-background-200 bg-background-50 px-3 py-3 sm:sticky sm:bottom-0 sm:z-10 sm:px-4">
        {dirty && <span className="mr-auto text-sm text-foreground-600">Unsaved changes</span>}
        <button type="button" className={`${btnSecondary} ${catalogueFocus}`} disabled={busy} onClick={onCancel}>Cancel</button>
        <button type="submit" className={`${btnPrimary} ${catalogueFocus}`}
          disabled={busy || Boolean(onReload) || !parentsReady || moduleRequired || (tutorRequired && !draft.tutor_id)}>
          {busy ? 'Saving…' : preview ? editor.record ? 'Update preview' : 'Add to preview'
            : canCreateTemplate ? 'Add module' : `${editor.record ? 'Update' : 'Create'} ${editorLabel(editor.resource)}`}
        </button>
        {onReload && <button type="button" className={`${btnSecondary} ${catalogueFocus}`} onClick={onReload}>Reload latest and discard edits</button>}
      </footer>
    </form>
  );
}
