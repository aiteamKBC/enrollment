import type { CatalogRecord, Cohort, Delivery, Detail, Group, Topic } from './catalogApi';
import type { EditorState } from './CatalogEditor';
import { calculatePricing, cohortDates, cohortSchedule, hundredths, inclusiveDays, isWholePoundAmount, moduleEnd, savedDeliveryEndOverride } from './catalogMath';

export type CatalogValues = Record<string, unknown>;
const current = (record?: CatalogRecord) => record && !record.archived_at;
const todayInLondon = () => new Intl.DateTimeFormat('sv-SE', { timeZone: 'Europe/London', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());

export function validateModuleKsbs(ids: string[], detail: Detail, previousIds: string[] = []) {
  const previous = new Set(previousIds);
  const allowed = new Set(detail.ksbs.filter(ksb => !ksb.archived_at || previous.has(ksb.id)).map(ksb => ksb.id));
  if (new Set(ids).size !== ids.length || ids.some(id => !allowed.has(id))) {
    throw new Error('Choose distinct KSBs from this programme. New links cannot use archived KSBs.');
  }
}

export function cohortScheduleForSave(values: CatalogValues, detail: Detail, saved?: Cohort) {
  const start = String(values.start_date ?? saved?.start_date ?? '');
  const epa = String(values.epa_period ?? saved?.epa_period ?? '').trim();
  const trainingMonths = 'training_duration_months' in values
    ? Number(values.training_duration_months) : saved?.training_duration_months ?? null;
  const trainingOverride = 'training_end_override' in values ? values.training_end_override as string | null : saved?.training_end_override ?? null;
  const apprenticeshipOverride = 'apprenticeship_end_override' in values ? values.apprenticeship_end_override as string | null : saved?.apprenticeship_end_override ?? null;
  const legacy = saved && trainingMonths == null;
  if (!legacy && (trainingMonths == null || !Number.isInteger(trainingMonths) || trainingMonths < 1 || trainingMonths > 120)) {
    throw new Error('Enter a planned duration between 1 and 120 months.');
  }
  if (saved) {
    const unchanged = start === saved.start_date && epa !== '' && Number(epa) === saved.epa_period &&
      trainingMonths === saved.training_duration_months;
    if (unchanged && trainingOverride === (saved.training_end_override ?? null) &&
      apprenticeshipOverride === (saved.apprenticeship_end_override ?? null)) return saved;
    if (legacy && !unchanged) throw new Error('Enter a planned duration to change the saved start date or EPA period.');
  }
  if (!epa || (!legacy && saved && saved.epa_unit !== 'months' && values.epa_period == null)) {
    throw new Error('Enter the EPA period in months. Enter 0 only when no additional EPA time is planned.');
  }
  if (trainingOverride && trainingOverride < start) throw new Error('Training end date must be on or after the cohort start date.');
  const options = {
    trainingOverride, apprenticeshipOverride,
    legacy: legacy ? { trainingEnd: saved.automatic_training_end_date ?? saved.training_end_date, epaUnit: saved.epa_unit } : undefined,
  };
  const { trainingEnd } = cohortDates(start, trainingMonths, null, options);
  if (trainingEnd && apprenticeshipOverride && apprenticeshipOverride < trainingEnd) {
    throw new Error('Apprenticeship end date must be on or after the training end date.');
  }
  const dates = cohortSchedule(start, trainingMonths, Number(epa), options);
  if (!dates) throw new Error('Enter valid dates and an EPA period within the supported calendar range.');
  if (saved && (start !== saved.start_date || dates.training_end_date !== saved.training_end_date) && detail.deliveries.some(delivery => current(delivery) &&
    detail.groups.some(group => current(group) && group.cohort_id === saved.id && group.id === delivery.group_id) &&
    (delivery.start_date < start || delivery.end_date > dates.training_end_date))) {
    throw new Error('All scheduled modules must fit within the cohort training dates.');
  }
  return { ...dates, start_date: start, epa_period: Number(epa) };
}

export function validateDraft(editor: EditorState, values: CatalogValues, detail: Detail | null, preview = false) {
  if (editor.resource !== 'deliveries' && editor.resource !== 'ksbs' && !String(values.name ?? '').trim()) {
    throw new Error('Enter a name.');
  }
  switch (editor.resource) {
    case 'programme':
      if (!calculatePricing({ total_cost: values.total_cost, epa_cost: values.epa_cost, lecture_hours: values.lecture_hours }, [], preview)) {
        throw new Error(`Enter ${preview ? 'whole-pound' : 'valid GBP'} amounts, an EPA cost no higher than the total, and hours per ${preview ? 'session' : 'lecture'} between 0.01 and 24.`);
      }
      if (values.funding_band_maximum != null &&
        (preview ? !isWholePoundAmount(values.funding_band_maximum) : hundredths(values.funding_band_maximum) === null)) {
        throw new Error(preview ? 'Enter the funding band maximum in whole pounds.' : 'Enter a valid funding band maximum in GBP.');
      }
      break;
    case 'cohorts': {
      if (!detail) throw new Error('Choose a programme before scheduling a cohort.');
      cohortScheduleForSave(values, detail, editor.record as Cohort | undefined);
      break;
    }
    case 'groups': {
      if (!editor.record && (values.cohort_id !== editor.parentId ||
        !detail?.cohorts.some(row => row.id === editor.parentId && current(row) && row.source_system === 'manual'))) {
        throw new Error('Choose an editable cohort in the selected programme.');
      }
      const group = editor.record as Group | undefined;
      const start = String(values.start_time ?? group?.start_time ?? '');
      const end = String(values.end_time ?? group?.end_time ?? '');
      const storedTime = /^(?:[01][0-9]|2[0-3]):[0-5][0-9](?::[0-5][0-9](?:\.\d{1,6})?)?$/;
      if (!storedTime.test(start) || !storedTime.test(end)) throw new Error('Enter start and end times in 24-hour HH:mm format.');
      const withSeconds = (time: string) => {
        const [clock, fraction = ''] = time.split('.');
        return `${clock.length === 5 ? `${clock}:00` : clock}.${fraction.padEnd(6, '0')}`;
      };
      if (withSeconds(start) >= withSeconds(end)) throw new Error('End time must be later than start time.');
      break;
    }
    case 'modules': {
      const topics = (values.weekly_topics ?? []) as Topic[];
      const limit = Number(values.weeks);
      if (preview && (!Number.isInteger(limit) || limit < 1 || limit > 520)) {
        throw new Error('Enter a number of sessions between 1 and 520.');
      }
      if (new Set(topics.map(topic => topic.week)).size !== topics.length ||
        topics.some(topic => !Number.isInteger(topic.week) || topic.week < 1 || topic.week > limit)) {
        throw new Error(preview ? 'Use distinct week numbers within the number of sessions.' : 'Use distinct week numbers within the module duration.');
      }
      if (topics.some(topic => !topic.content.trim())) throw new Error('Enter content for each weekly topic.');
      break;
    }
    case 'ksbs':
      if (!String(values.description ?? '').trim()) throw new Error('Enter a description for the KSB.');
      break;
    case 'deliveries': {
      if (!detail) throw new Error('Choose a programme to schedule a module.');
      const previous = editor.record as Delivery | undefined;
      const groupId = previous?.group_id ?? editor.parentId;
      const group = detail.groups.find(row => row.id === groupId && current(row));
      const cohort = detail.cohorts.find(row => row.id === group?.cohort_id && current(row));
      if (group?.source_system !== 'manual' || cohort?.source_system !== 'manual' ||
        (!previous && values.group_id !== groupId) || (values.cohort_id !== undefined && values.cohort_id !== cohort.id)) {
        throw new Error('Choose an editable group in the selected cohort.');
      }
      if ((!previous || 'tutor_id' in values) && !values.tutor_id) throw new Error('Select a tutor to schedule this module.');
      const template = values.new_template as CatalogValues | undefined;
      let weeks: number;
      if (template) {
        if (previous || values.module_id || values.module_revision_id) throw new Error('Choose an existing template or create a new one.');
        validateDraft({ resource: 'modules' }, template, detail, true);
        validateModuleKsbs((template.ksb_ids ?? []) as string[], detail);
        weeks = Number(template.weeks);
      } else {
        const module = detail.modules.find(row => row.id === values.module_id && current(row));
        const pinned = previous && previous.module_id === values.module_id && previous.curriculum.id === values.module_revision_id;
        if (!pinned && (!module || module.current_revision?.id !== values.module_revision_id)) throw new Error('Choose a current module revision.');
        if (previous && !pinned && previous.start_date <= todayInLondon()) {
          throw new Error('A delivery that has started keeps its recorded curriculum revision.');
        }
        if (detail.deliveries.some(row => current(row) && row.group_id === groupId && row.module_id === values.module_id && row.id !== previous?.id)) {
          throw new Error('This module is already scheduled in this group.');
        }
        weeks = pinned ? previous.curriculum.weeks : module!.weeks;
      }
      const start = String(values.start_date ?? previous?.start_date ?? '');
      const override = 'end_date' in values ? values.end_date as string | null : savedDeliveryEndOverride(previous);
      const end = override ?? moduleEnd(start, weeks) ?? '';
      if (!inclusiveDays(start, end)) throw new Error('Enter valid dates. End date must be on or after the start date.');
      if (start < cohort.start_date || end > cohort.training_end_date) {
        throw new Error('The scheduled module must fit within the cohort training dates.');
      }
      break;
    }
  }
}
