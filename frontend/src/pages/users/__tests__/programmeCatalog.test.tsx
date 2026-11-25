import type { ReactNode } from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Link, MemoryRouter, useNavigate } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import ProgrammeCatalogPage from '../programme-structure/catalogPage';
import { CatalogEditor } from '../programme-structure/CatalogEditor';
import type { Detail, Pricing, Programme } from '../programme-structure/catalogApi';

vi.mock('@/pages/admin/_shared/AdminPage', () => ({
  AdminPage: ({ title, actions, children }: { title: string; actions: ReactNode; children: ReactNode }) =>
    <main><h1>{title}</h1>{actions}{children}</main>,
  DataPanel: ({ children, loading, error, empty, emptyMessage }: {
    children: ReactNode; loading: boolean; error?: string; empty?: boolean; emptyMessage?: string;
  }) => loading ? <p>Loading</p> : error ? <p role="alert">{error}</p> : empty ? <p>{emptyMessage}</p> : children,
}));
vi.mock('@/features/programme-suitability/api', () => ({
  prefetchAssessmentSecurity: async () => ({ csrfToken: 'fixture' }),
}));
const base = '/enrolment_api/programme-structure/catalog/';
const metadata = { source_system: 'manual', archived_at: null, external_id: '', revision: 1 };
const programme: Programme = {
  ...metadata, id: 'programme-fixture', name: 'Fixture programme', code: 'TEST', status: 'draft', level: 4,
  total_cost: '100.00', epa_cost: '20.00', description: '', epao: '', lecture_hours: '2.50',
  funding_band_maximum: null,
};
const detail: Detail = {
  programme,
  cohorts: [
    { ...metadata, id: 'first', name: 'First cohort', start_date: '2027-01-01', training_end_date: '2027-01-31',
      epa_period: 1, epa_unit: 'months', apprenticeship_end_date: '2027-02-28', duration_days: 31,
      automatic_training_end_date: '2027-01-31', training_end_override: null, apprenticeship_end_override: null,
      training_duration_months: 1, apprenticeship_duration_months: 2 },
    { ...metadata, id: 'second', name: 'Second cohort', start_date: '2027-02-01', training_end_date: '2027-02-28',
      epa_period: 1, epa_unit: 'months', apprenticeship_end_date: '2027-03-31', duration_days: 28,
      automatic_training_end_date: '2027-02-28', training_end_override: null, apprenticeship_end_override: null,
      training_duration_months: 1, apprenticeship_duration_months: 2 },
  ],
  groups: [
    { ...metadata, id: 'first-group', name: 'First group', cohort_id: 'first', coach_id: null, weekday: 1, start_time: '09:00', end_time: '10:00' },
    { ...metadata, id: 'second-group', name: 'Second group', cohort_id: 'second', coach_id: null, weekday: 3, start_time: '09:00', end_time: '10:00' },
  ],
  deliveries: [], modules: [], ksbs: [],
};
const pricing: Pricing = {
  total_cost: '100.00', base_cost: '80.00', total_training: '64.00', training_hours: null,
  allocations: { kbc_training: '32.00', distance_training: '32.00', assessment: '12.00', materials: '4.00', epa: '20.00' },
  counts: { lectures: null, reviews: null, books: null }, rates: { training_hour: null, review: null, book: null },
};
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
const mount = () => render(<MemoryRouter><ProgrammeCatalogPage /></MemoryRouter>);

afterEach(() => vi.unstubAllGlobals());

it('runs the complete temporary workflow without network or browser storage and starts empty on remount', async () => {
  const fetchMock = vi.fn();
  vi.stubGlobal('fetch', fetchMock);
  const storage = vi.spyOn(Storage.prototype, 'setItem');
  const user = userEvent.setup();
  const preview = () => render(<MemoryRouter><ProgrammeCatalogPage preview /></MemoryRouter>);
  const instance = preview();
  const click = (name: string) => user.click(screen.queryByRole('button', { name }) ??
    within(screen.getByRole('navigation', { name: 'Breadcrumb' })).getByRole('link', { name }));
  const type = (label: string, value: string) => user.type(screen.getByLabelText(label, { exact: true }), value);
  const change = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label, { exact: true }), { target: { value } });
  const addPreviewStaff = async (label: string, name: string, email: string) => {
    await click(`Add preview ${label.toLowerCase()}`);
    await type(`Preview ${label.toLowerCase()} name`, name);
    await type(`Preview ${label.toLowerCase()} email (optional)`, email);
    await click('Add to preview staff');
    await user.click(screen.getByRole('combobox', { name: label }));
    await type(`Search ${label.toLowerCase()} by name or email`, email);
    await user.click(screen.getByRole('option', { name: new RegExp(name) }));
    expect(screen.getByRole('combobox', { name: label })).toHaveTextContent(name);
  };
  expect(screen.getByText('Interactive Preview')).toBeVisible();
  expect(screen.getByText(/No programmes to show/)).toBeVisible();
  expect(screen.queryByRole('link', { name: 'Legacy Programme Structure' })).not.toBeInTheDocument();
  try {
    await click('Add programme');
    expect(screen.queryByLabelText(/EPA period/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Planned duration (months)')).not.toBeInTheDocument();
    expect(screen.getByLabelText('EPA organisation')).toBeVisible();
    await type('Programme name', 'Review programme');
    await type('Programme code', 'review');
    await user.selectOptions(screen.getByLabelText('Level', { exact: true }), '4');
    await type('Total per apprentice (GBP)', '11000');
    await type('EPA cost (GBP)', '1000');
    await click('Add to preview');
    expect(screen.getByRole('heading', { name: 'Review programme' })).toBeVisible();
    await click('Pricing breakdown');
    const priceTable = screen.getByRole('table', { name: 'Cost allocation and reconciliation' });
    expect(within(priceTable).getByText('£11,000')).toBeVisible();
    change('Total programme cost (GBP)', '21000.25');
    expect(screen.getByRole('alert')).toHaveTextContent('Enter whole-pound amounts');
    change('Total programme cost (GBP)', '21000');
    expect(within(priceTable).getByText('£21,000')).toBeVisible();
    change('Funding band maximum (GBP)', '20000');
    expect(screen.getByText(/£1,000 above the funding band maximum/)).toHaveTextContent('This does not determine the employer balance.');
    await click('Reset inputs');
    await click('Cohorts');
    await click('Add cohort');
    expect(screen.getByLabelText('Programme')).toHaveDisplayValue('Review programme · REVIEW');
    await type('Cohort name', 'January intake');
    change('Start date', '2027-01-01');
    expect(screen.getByLabelText('Planned duration (months)')).toBeRequired();
    expect(screen.getByLabelText('Training end date')).toHaveValue('');
    await type('Planned duration (months)', '1');
    expect(screen.getByLabelText('Training end date')).toHaveValue('2027-01-31');
    expect(screen.queryByLabelText('EPA period unit')).not.toBeInTheDocument();
    expect(screen.getByLabelText('EPA period (months)', { exact: true })).toHaveValue(null);
    change('EPA period (months)', '1');
    expect(screen.getByLabelText('Apprenticeship end date')).toHaveValue('2027-02-28');
    expect(within(screen.getByText('Total apprenticeship duration').closest('div')!).getByText('2 months')).toBeVisible();
    change('Training end date', '2027-02-01');
    expect(screen.getByLabelText('Apprenticeship end date')).toHaveValue('2027-03-01');
    change('Apprenticeship end date', '2027-03-15');
    await click('Add to preview');
    await click('Edit January intake');
    expect(screen.getAllByText('Manually overridden')).toHaveLength(2);
    expect(screen.getByLabelText('Training end date')).toHaveValue('2027-02-01');
    expect(screen.getByLabelText('Apprenticeship end date')).toHaveValue('2027-03-15');
    await click('Reset training end date to automatic');
    expect(screen.getByLabelText('Apprenticeship end date')).toHaveValue('2027-03-15');
    await click('Reset apprenticeship end date to automatic');
    expect(screen.getByLabelText('Apprenticeship end date')).toHaveValue('2027-02-28');
    await click('Update preview');
    await click('Add group');
    expect(screen.getByLabelText('Programme')).toHaveDisplayValue('Review programme · REVIEW');
    expect(screen.getByLabelText('Cohort')).toHaveDisplayValue('January intake');
    await type('Group name', 'Monday group');
    await addPreviewStaff('Skills Coach', 'Temporary Coach', 'coach@preview.test');
    await user.selectOptions(screen.getByLabelText('Day of week'), '1');
    await click('Choose start time');
    const startHour = screen.getByRole('combobox', { name: 'Choose start time hour' });
    expect(screen.getByRole('textbox', { name: 'Start time hour' })).toHaveFocus();
    expect(within(startHour).getByRole('option', { name: '00' })).toBeInTheDocument();
    expect(within(startHour).getByRole('option', { name: '23' })).toBeInTheDocument();
    await user.selectOptions(startHour, '09');
    expect(screen.getByRole('button', { name: 'Set start time' })).toBeDisabled();
    const startMinute = screen.getByRole('combobox', { name: 'Choose start time minute' });
    expect(within(startMinute).getByRole('option', { name: '59' })).toBeInTheDocument();
    await user.selectOptions(startMinute, '00');
    await click('Set start time');
    expect(screen.getByRole('button', { name: 'Choose start time' })).toHaveFocus();
    expect(screen.getByLabelText('Start time')).toHaveValue('09:00');
    await type('End time', '1111');
    expect(screen.getByLabelText('End time')).toHaveValue('11:11');
    await click('Add to preview');
    expect(screen.getByText('Monday · 09:00–11:11')).toBeVisible();
    expect(screen.getByText('Temporary Coach')).toBeVisible();
    await click('Open module library');
    await user.click(screen.getByRole('tab', { name: /^KSB Library/ }));
    await click('Add KSB');
    await type('KSB code', 'K1');
    await type('Description', 'Planning criteria');
    await click('Add to preview');
    await click('Cohorts');
    await click('January intake');
    await click('Monday group');
    await click('Add module');
    expect(screen.getByRole('combobox', { name: 'Programme' })).toHaveTextContent('Review programme');
    expect(screen.getByLabelText('Cohort')).toHaveDisplayValue('January intake');
    expect(screen.getByLabelText('Group')).toHaveDisplayValue('Monday group');
    expect(screen.getByRole('radio', { name: 'Create new module' })).toBeChecked();
    await type('Module name', 'Planning template');
    expect(screen.queryByLabelText('Number of weeks')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Number of lectures')).not.toBeInTheDocument();
    await type('Number of sessions', '2');
    await type('Progress reviews', '2');
    await type('Number of books', '1');
    await user.click(screen.getByRole('checkbox', { name: /K1/ }));
    await user.type(screen.getByRole('searchbox', { name: 'Search KSBs' }), 'no match');
    expect(screen.getByText('1 selected')).toBeVisible();
    expect(screen.queryByRole('checkbox', { name: /K1/ })).not.toBeInTheDocument();
    await user.clear(screen.getByRole('searchbox', { name: 'Search KSBs' }));
    expect(screen.getByRole('checkbox', { name: /K1/ })).toBeChecked();
    expect(screen.getByRole('button', { name: 'Remove K1 from selection' })).toBeVisible();
    await click('Add weekly topic');
    await type('Topic content', 'Original week one');
    await click('Add weekly topic');
    const secondWeek = within(screen.getByText('Week 2', { exact: true }).closest('details')!);
    await user.type(secondWeek.getByLabelText('Topic content'), 'Move this topic first');
    expect(screen.getByRole('button', { name: 'Add weekly topic' })).toBeDisabled();
    await user.click(secondWeek.getByRole('button', { name: 'Move up week 2' }));
    await click('Next to schedule and tutor');
    expect(screen.getByRole('heading', { name: 'Schedule and tutor' })).toHaveFocus();
    change('Start date', '2027-01-04');
    expect(screen.getByText('No preview tutors added.')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Add to preview' })).toBeDisabled();
    await addPreviewStaff('Tutor', 'Temporary Tutor', 'tutor@preview.test');
    expect(screen.getByLabelText('End date')).toHaveValue('2027-01-17');
    change('End date', '2027-01-03');
    await click('Add to preview');
    expect(screen.getByRole('alert')).toHaveTextContent('End date must be on or after the start date.');
    change('End date', '2027-02-01');
    await click('Add to preview');
    expect(screen.getByRole('alert')).toHaveTextContent('must fit within the cohort training dates.');
    change('End date', '2027-01-20');
    await click('Add to preview');
    expect(screen.getByText('Temporary Tutor')).toBeVisible();
    expect(screen.getByText('20 Jan 2027')).toBeVisible();
    expect(screen.getByText(/Pinned revision v1/)).toBeVisible();
    expect(screen.getByText(/Planning criteria/)).toBeVisible();
    const firstWeek = within(screen.getByText('Week 1', { exact: true }).closest('details')!);
    expect(firstWeek.getByText('Move this topic first')).toBeInTheDocument();
    await click('Edit Planning template');
    expect(screen.getByLabelText('End date')).toHaveValue('2027-01-20');
    await click('Reset end date to automatic');
    expect(screen.getByLabelText('End date')).toHaveValue('2027-01-17');
    change('Start date', '2027-01-05');
    expect(screen.getByLabelText('End date')).toHaveValue('2027-01-18');
    await click('Update preview');
    expect(screen.getByText('18 Jan 2027')).toBeVisible();
    await click('Edit Planning template');
    expect(screen.getByText('Auto-calculated')).toBeVisible();
    expect(screen.getByLabelText('End date')).toHaveValue('2027-01-18');
    await click('Cancel');
    await click('Review programme');
    await click('Pricing breakdown');
    await user.selectOptions(screen.getByRole('combobox', { name: 'Cohort' }), within(screen.getByRole('combobox', { name: 'Cohort' })).getByRole('option', { name: 'January intake' }));
    await user.selectOptions(screen.getByRole('combobox', { name: 'Group' }), within(screen.getByRole('combobox', { name: 'Group' })).getByRole('option', { name: 'Monday group' }));
    expect(screen.getByText('£1,600')).toBeVisible();
    expect(within(screen.getByText('Progress review unit price').closest('div')!).getByText('£750')).toBeVisible();
    expect(within(screen.getByText('Book unit price').closest('div')!).getByText('£500')).toBeVisible();
    await click('Edit Review programme');
    await user.clear(screen.getByLabelText('Programme name'));
    await type('Programme name', 'Updated programme');
    await click('Update preview');
    await click('Programme catalogue');
    await user.type(screen.getByRole('searchbox', { name: 'Search programmes' }), 'no match');
    expect(screen.getByText('No programmes match your search.')).toBeVisible();
    await user.clear(screen.getByRole('searchbox', { name: 'Search programmes' }));
    await click('Updated programme');
    await click('Cohorts');
    await user.type(screen.getByRole('searchbox', { name: 'Search cohorts' }), 'missing');
    expect(screen.getByText('No cohorts match your search.')).toBeVisible();
    await user.clear(screen.getByRole('searchbox', { name: 'Search cohorts' }));
    await click('January intake');
    await click('Monday group');
    await click('Remove Monday group');
    await click('Remove from preview');
    expect(screen.getByText(/No groups in this cohort/)).toBeVisible();
  } finally {
    expect(fetchMock).not.toHaveBeenCalled();
    expect(storage).not.toHaveBeenCalled();
    storage.mockRestore();
    instance.unmount();
  }
  preview();
  expect(screen.getByText(/No programmes to show/)).toBeVisible();
}, 20_000);

it('creates a cohort under the programme chosen from the live catalogue', async () => {
  const other = { ...programme, id: 'other-programme', code: 'OTHER', name: 'Other programme' };
  const otherDetail: Detail = { ...detail, programme: other, cohorts: [], groups: [] };
  const post = vi.fn();
  vi.stubGlobal('fetch', vi.fn(async (url: string, request?: RequestInit) => {
    if (url === base + 'staff/') return json({ coaches: [], tutors: [] });
    if (url === base + 'programmes/') return json({ programmes: [programme, other] });
    if (url === base + `programmes/${other.id}/`) return json(otherDetail);
    if (request?.method === 'POST') {
      post(url, JSON.parse(String(request.body)));
      const cohort = { ...detail.cohorts[0], id: 'new-cohort', name: 'New cohort',
        training_end_date: '2027-02-28', apprenticeship_end_date: '2027-02-28',
        epa_period: 0, training_duration_months: 2, apprenticeship_duration_months: 2, duration_days: 59 };
      otherDetail.cohorts = [cohort];
      return json(cohort, 201);
    }
    throw new Error(`Unexpected request: ${url}`);
  }));
  const user = userEvent.setup();
  mount();
  await user.click(await screen.findByRole('button', { name: 'Add cohort' }));
  expect(screen.getByLabelText('Programme')).toHaveValue('');
  expect(screen.getByRole('button', { name: 'Create cohort' })).toBeDisabled();
  await user.selectOptions(screen.getByLabelText('Programme'), other.id);
  expect(screen.getByLabelText('Planned duration (months)')).toHaveValue(null);
  await user.type(screen.getByLabelText('Cohort name'), 'New cohort');
  fireEvent.change(screen.getByLabelText('Start date'), { target: { value: '2027-01-01' } });
  await user.type(screen.getByLabelText('Planned duration (months)'), '2');
  await user.type(screen.getByLabelText('EPA period (months)'), '0');
  await user.click(screen.getByRole('button', { name: 'Create cohort' }));
  expect(await screen.findByRole('heading', { name: 'New cohort' })).toBeVisible();
  expect(within(screen.getByRole('navigation', { name: 'Breadcrumb' })).getByText('Other programme')).toBeVisible();
  expect(post).toHaveBeenCalledExactlyOnceWith(base + `programmes/${other.id}/cohorts/`, {
    name: 'New cohort', start_date: '2027-01-01', training_duration_months: '2', epa_period: '0',
  });
});

it('lists only cohorts within the selected programme when adding a group', async () => {
  const empty = { ...programme, id: 'empty-programme', name: 'Empty programme', code: 'EMPTY' };
  vi.stubGlobal('fetch', vi.fn(async (url: string) => {
    if (url === base + 'staff/') return json({ coaches: [], tutors: [] });
    if (url === base + 'programmes/') return json({ programmes: [programme, empty] });
    if (url === base + `programmes/${empty.id}/`) return json({ ...detail, programme: empty, cohorts: [], groups: [] });
    if (url === base + `programmes/${programme.id}/`) return json(detail);
    throw new Error(`Unexpected request: ${url}`);
  }));
  const user = userEvent.setup();
  mount();
  await user.click(await screen.findByRole('button', { name: 'Add group' }));
  expect(screen.getByLabelText('Cohort')).toBeDisabled();
  await user.selectOptions(screen.getByLabelText('Programme'), programme.id);
  await screen.findByRole('option', { name: 'First cohort' });
  await user.selectOptions(screen.getByLabelText('Cohort'), 'first');
  await user.selectOptions(screen.getByLabelText('Programme'), empty.id);
  expect(await screen.findByText('Add a cohort to this programme before continuing.')).toBeVisible();
  expect(screen.getByLabelText('Cohort')).toHaveValue('');
  expect(screen.getByLabelText('Cohort')).toBeDisabled();
  expect(screen.queryByRole('option', { name: 'First cohort' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Create group' })).toBeDisabled();
});

it('resets incompatible choices and posts a delivery to the selected live hierarchy', async () => {
  const tutor = { id: 'assigned-tutor', name: 'Assigned Tutor', email: 'tutor@example.test' };
  const firstModule = { ...metadata, id: 'first-template', name: 'First template', weeks: 2, books: 1, reviews: 1,
    lectures: 1, ksb_ids: [], weekly_topics: [], current_revision: { id: 'first-revision', number: 1 } };
  const other = { ...programme, id: 'other-programme', code: 'OTHER', name: 'Other programme' };
  const otherCohort = { ...detail.cohorts[0], id: 'other-cohort', name: 'Other cohort' };
  const otherGroup = { ...detail.groups[0], id: 'other-group', name: 'Other group', cohort_id: otherCohort.id };
  const ksb = { ...metadata, id: 'other-ksb', code: 'S1', description: 'Review project scope' };
  const otherModule = { ...firstModule, id: 'other-template', name: 'Other template', current_revision: { id: 'other-revision', number: 1 },
    ksb_ids: [ksb.id], weekly_topics: [{ week: 1, content: 'Confirm the plan' }] };
  const otherDetail: Detail = { programme: other, cohorts: [otherCohort], groups: [otherGroup], modules: [otherModule], deliveries: [], ksbs: [ksb] };
  const post = vi.fn();
  vi.stubGlobal('fetch', vi.fn(async (url: string, request?: RequestInit) => {
    if (url === base + 'staff/') return json({ coaches: [], tutors: [tutor] });
    if (url === base + 'programmes/') return json({ programmes: [programme, other] });
    if (url === base + `programmes/${programme.id}/`) return json({ ...detail, modules: [firstModule] });
    if (url === base + `programmes/${other.id}/`) return json(otherDetail);
    if (request?.method === 'POST') {
      post(url, JSON.parse(String(request.body)));
      const delivery = { ...metadata, id: 'saved-delivery', group_id: otherGroup.id, module_id: otherModule.id,
        start_date: '2027-01-04', end_date: '2027-01-17', end_date_override: null, tutor_id: tutor.id,
        curriculum: { ...otherModule, ...otherModule.current_revision, ksbs: [ksb] } };
      otherDetail.deliveries = [delivery];
      return json(delivery, 201);
    }
    throw new Error(`Unexpected request: ${url}`);
  }));
  const user = userEvent.setup();
  const select = (label: string, value: string) => user.selectOptions(screen.getByLabelText(label), value);
  const selectProgramme = async (programmeName: string, code: string) => {
    await user.click(screen.getByRole('combobox', { name: 'Programme' }));
    await user.type(screen.getByLabelText('Search programmes by name or code'), code);
    await user.click(screen.getByRole('option', { name: new RegExp(programmeName) }));
  };
  mount();
  await user.click(await screen.findByRole('button', { name: 'Add module' }));
  expect(screen.getByLabelText('Group')).toBeDisabled();
  await selectProgramme(programme.name, programme.code);
  await screen.findByRole('option', { name: 'First cohort' });
  await select('Cohort', 'first');
  await select('Group', 'first-group');
  await select('Module and revision', 'first-revision');
  await selectProgramme(programme.name, programme.code);
  expect(screen.getByLabelText('Group')).toHaveValue('first-group');
  await select('Cohort', 'second');
  expect(screen.getByLabelText('Group')).toHaveValue('');
  expect(within(screen.getByLabelText('Group')).queryByRole('option', { name: 'First group' })).not.toBeInTheDocument();
  expect(screen.getByLabelText('Module and revision')).toHaveValue('first-revision');
  await select('Group', 'second-group');
  await selectProgramme(other.name, other.code);
  await screen.findByRole('option', { name: 'Other cohort' });
  expect(screen.getByLabelText('Cohort')).toHaveValue('');
  expect(screen.getByLabelText('Group')).toHaveValue('');
  expect(screen.getByLabelText('Module and revision')).toHaveValue('');
  expect(screen.queryByRole('option', { name: /First template/ })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Add module' })).toBeDisabled();
  await select('Cohort', otherCohort.id);
  await select('Group', otherGroup.id);
  await select('Module and revision', otherModule.current_revision.id);
  const selected = within(screen.getByRole('region', { name: 'Selected module curriculum' }));
  expect(selected.getByText('v1')).toBeVisible();
  await user.click(selected.getByText('View KSBs and weekly topics'));
  expect(selected.getByText(/Review project scope/)).toBeVisible();
  await user.click(selected.getByText('Week 1'));
  expect(selected.getByText('Confirm the plan')).toBeVisible();
  fireEvent.change(screen.getByLabelText('Start date'), { target: { value: '2027-01-04' } });
  await user.click(screen.getByRole('combobox', { name: 'Tutor' }));
  await user.click(screen.getByRole('option', { name: /Assigned Tutor/ }));
  await user.click(screen.getByRole('button', { name: 'Add module' }));
  expect(await screen.findByRole('heading', { name: 'Other template' })).toBeVisible();
  const breadcrumbs = within(screen.getByRole('navigation', { name: 'Breadcrumb' }));
  expect(breadcrumbs.getByText('Other programme')).toBeVisible();
  expect(breadcrumbs.getByText('Other cohort')).toBeVisible();
  expect(breadcrumbs.getByText('Other group')).toBeVisible();
  expect(post).toHaveBeenCalledExactlyOnceWith(base + `programmes/${other.id}/deliveries/`, {
    cohort_id: otherCohort.id, group_id: otherGroup.id, module_id: otherModule.id,
    module_revision_id: otherModule.current_revision.id, start_date: '2027-01-04', end_date: null, tutor_id: tutor.id,
  });
});

it('loads the saved duration and recalculates automatic dates when the cohort duration changes', async () => {
  const onSave = vi.fn();
  const user = userEvent.setup();
  render(<CatalogEditor editor={{ resource: 'cohorts', record: detail.cohorts[0] }}
    detail={detail} staff={null} busy={false} error="" onSave={onSave} onCancel={vi.fn()} />);
  const duration = screen.getByLabelText('Planned duration (months)');
  expect(duration).toHaveValue(1);
  expect(screen.getByLabelText('Apprenticeship end date')).toHaveValue('2027-02-28');
  await user.click(screen.getByRole('button', { name: 'Update cohort' }));
  expect(onSave).toHaveBeenCalledWith({ name: 'First cohort', start_date: '2027-01-01', epa_period: '1' });
  fireEvent.change(duration, { target: { value: '12' } });
  expect(screen.getByLabelText('Training end date')).toHaveValue('2027-12-31');
  expect(screen.getByLabelText('Apprenticeship end date')).toHaveValue('2028-01-31');
  await user.click(screen.getByRole('button', { name: 'Update cohort' }));
  expect(onSave).toHaveBeenLastCalledWith({ name: 'First cohort', start_date: '2027-01-01', epa_period: '1', training_duration_months: '12' });
});

it('does not convert a legacy EPA period or change dates on a name-only save', async () => {
  const onSave = vi.fn();
  const user = userEvent.setup();
  render(<CatalogEditor editor={{ resource: 'cohorts', record: { ...detail.cohorts[0],
    epa_period: 2, epa_unit: 'weeks', apprenticeship_end_date: '2027-02-14',
    training_duration_months: null, apprenticeship_duration_months: null,
  } as Detail['cohorts'][number] }} detail={detail} staff={null} busy={false} error="" onSave={onSave} onCancel={vi.fn()} />);
  expect(screen.getByText('2 weeks')).toBeVisible();
  expect(screen.getByLabelText('Apprenticeship end date')).toHaveValue('2027-02-14');
  await user.click(screen.getByRole('button', { name: 'Update cohort' }));
  expect(onSave).toHaveBeenCalledWith({ name: 'First cohort' });
  fireEvent.change(screen.getByLabelText('Training end date'), { target: { value: '2027-02-05' } });
  expect(screen.getByLabelText('Apprenticeship end date')).toHaveValue('2027-02-19');
  await user.click(screen.getByRole('button', { name: 'Update cohort' }));
  expect(onSave).toHaveBeenLastCalledWith({ name: 'First cohort', training_end_override: '2027-02-05' });
  await user.type(screen.getByLabelText('Planned duration (months)'), '2');
  expect(screen.getByLabelText('Start date')).toHaveValue('2027-01-01');
  expect(screen.getByLabelText('EPA period (months)')).toHaveValue(null);
  await user.type(screen.getByLabelText('EPA period (months)'), '1');
  expect(screen.getByLabelText('Training end date')).toHaveValue('2027-02-05');
  expect(screen.getByLabelText('Apprenticeship end date')).toHaveValue('2027-03-05');
});

it('shows date deviations and rejects an invalid override before sending a live patch', async () => {
  const user = userEvent.setup();
  const patch = vi.fn();
  const scope = base + `programmes/${programme.id}/`;
  vi.stubGlobal('fetch', vi.fn(async (url: string, request?: RequestInit) => {
    if (url === base + 'staff/') return json({ coaches: [], tutors: [] });
    if (url === base + 'programmes/') return json({ programmes: [programme] });
    if (url === scope) return json(detail);
    if (request?.method === 'PATCH' && url === scope + 'cohorts/first/') {
      patch(JSON.parse(String(request.body)), new Headers(request.headers).get('If-Match'));
      return json({ error: 'This record was updated elsewhere.', code: 'catalog_revision_conflict' }, 409);
    }
    throw new Error(`Unexpected request: ${url}`);
  }));
  const click = async (name: string) => user.click(await screen.findByRole('button', { name }));
  const change = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } });
  mount();
  await click(programme.name);
  await click('Cohorts');
  await click('First cohort');
  await click('Edit First cohort');
  change('Training end date', '2027-02-02');
  expect(screen.getByText('The cohort’s planned duration gives 31 Jan 2027.')).toBeVisible();
  expect(screen.getByLabelText('Apprenticeship end date')).toHaveValue('2027-03-02');
  change('Apprenticeship end date', '2027-02-01');
  expect(screen.getByLabelText('Apprenticeship end date')).toHaveAttribute('aria-invalid', 'true');
  await click('Update cohort');
  expect(patch).not.toHaveBeenCalled();
  change('Apprenticeship end date', '2027-03-10');
  expect(screen.getByText('The scheduled EPA period gives 2 Mar 2027.')).toBeVisible();
  await click('Update cohort');
  expect(await screen.findByRole('button', { name: 'Reload latest and discard edits' })).toBeVisible();
  expect(patch).toHaveBeenCalledExactlyOnceWith({
    name: 'First cohort', start_date: '2027-01-01', epa_period: '1',
    training_end_override: '2027-02-02', apprenticeship_end_override: '2027-03-10',
  }, '"1"');
  expect(screen.getByLabelText('Apprenticeship end date')).toHaveValue('2027-03-10');
});

it('searches eligible live coaches and tutors and saves their UUIDs without changing stored seconds', async () => {
  const coach = { id: 'b9dbd165-fa8f-433c-91a9-a699745624f3', name: 'Current Coach', email: 'coach@example.test' };
  const tutor = { id: 'dccfa1a3-edec-47d5-a26a-f6f2460d68a3', name: 'Current Tutor', email: 'tutor@example.test' };
  const group = { ...detail.groups[0], coach_id: 'previous', start_time: '09:30:15', end_time: '11:00:38' };
  const curriculum = { id: 'revision', number: 1, name: 'Test delivery', weeks: 1, books: null, reviews: null, lectures: null, ksbs: [], weekly_topics: [] };
  const delivery = { ...metadata, id: 'delivery', group_id: group.id, module_id: 'template', curriculum,
    start_date: '2027-01-04', end_date: '2027-01-10', tutor_id: null };
  const data = { ...detail, groups: [group], deliveries: [delivery] };
  const scope = base + `programmes/${programme.id}/`;
  let fail = true;
  const patch = vi.fn();
  vi.stubGlobal('fetch', vi.fn(async (url: string, request?: RequestInit) => {
    if (url === base + 'staff/') return fail ? json({ error: 'Failed' }, 503) : json({ coaches: [coach], tutors: [tutor] });
    if (url === base + 'programmes/') return json({ programmes: [programme] });
    if (url === scope) return json(data);
    if (request?.method === 'PATCH') {
      const values = JSON.parse(String(request.body));
      patch(url, values);
      if (url === scope + `groups/${group.id}/`) return json(Object.assign(group, values));
      if (url === scope + `deliveries/${delivery.id}/`) return json(Object.assign(delivery, values));
    }
    throw new Error(`Unexpected request: ${url}`);
  }));
  const user = userEvent.setup();
  const click = async (name: string) => user.click(await screen.findByRole('button', { name }));
  const choose = async (label: string, query: string, name: string) => {
    await user.click(screen.getByRole('combobox', { name: label }));
    const search = screen.getByLabelText(`Search ${label.toLowerCase()} by name or email`);
    await user.type(search, 'no matches');
    expect(screen.getByText(label === 'Tutor' ? 'No matching tutors.' : 'No matching skills coaches.')).toBeVisible();
    await user.clear(search);
    await user.type(search, query);
    await user.click(screen.getByRole('option', { name: new RegExp(name) }));
    expect(screen.getByRole('combobox', { name: label })).toHaveTextContent(name);
  };
  mount();
  await click(programme.name);
  await click('Cohorts');
  await click('First cohort');
  await click(group.name);
  expect(screen.getByText('Monday · 09:30–11:00')).toBeVisible();
  await click(`Edit ${group.name}`);
  expect(screen.getByLabelText('Start time')).toHaveValue('09:30');
  expect(screen.getByLabelText('End time')).toHaveValue('11:00');
  expect(screen.getByText('Could not load staff options. Existing assignments are kept.')).toBeVisible();
  fail = false;
  await click('Retry staff directory');
  expect(await screen.findByText(/This assignment is inactive or no longer eligible/)).toBeVisible();
  await choose('Skills Coach', coach.email, coach.name);
  await click('Update group');
  expect(patch).toHaveBeenLastCalledWith(scope + `groups/${group.id}/`, { coach_id: coach.id, weekday: '1', name: group.name });
  await click(`Edit ${group.name}`);
  await click('Choose end time');
  expect(screen.getByRole('textbox', { name: 'End time hour' })).toHaveValue('11');
  expect(screen.getByRole('textbox', { name: 'End time minute' })).toHaveValue('00');
  await user.keyboard('{Escape}');
  expect(screen.getByRole('button', { name: 'Choose end time' })).toHaveFocus();
  const end = screen.getByLabelText('End time');
  await user.clear(end);
  await user.type(end, '24:00');
  expect(end).toBeInvalid();
  await click('Update group');
  expect(patch).toHaveBeenCalledTimes(1);
  await user.clear(end);
  await user.type(end, '09:30');
  await click('Update group');
  expect(await screen.findByText('End time must be later than start time.')).toBeVisible();
  expect(patch).toHaveBeenCalledTimes(1);
  await click('Choose end time');
  const minute = screen.getByRole('textbox', { name: 'End time minute' });
  await user.clear(minute);
  await user.type(minute, '60{Enter}');
  expect(minute).toBeInvalid();
  expect(screen.getByRole('button', { name: 'Set end time' })).toBeDisabled();
  expect(screen.getByText('Enter 00 to 59.')).toBeVisible();
  await user.clear(minute);
  await user.type(minute, '31{Enter}');
  expect(end).toHaveValue('09:31');
  expect(patch).toHaveBeenCalledTimes(1);
  await click('Update group');
  expect(patch).toHaveBeenLastCalledWith(scope + `groups/${group.id}/`, { end_time: '09:31', weekday: '1', name: group.name });
  expect(await screen.findByText('Monday · 09:30–09:31')).toBeVisible();
  await click(curriculum.name);
  await click(`Edit ${curriculum.name}`);
  await choose('Tutor', 'current tut', tutor.name);
  await click('Update scheduled module');
  expect(patch).toHaveBeenLastCalledWith(scope + `deliveries/${delivery.id}/`, {
    tutor_id: tutor.id, start_date: delivery.start_date, module_id: delivery.module_id, module_revision_id: curriculum.id,
  });
  await click(`Edit ${curriculum.name}`);
  expect(screen.getByRole('combobox', { name: 'Tutor' })).toHaveTextContent(tutor.name);
});

it('shows required setup and retries directly into the live programme list', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(json({ error: 'Setup required.', code: 'catalog_schema_not_ready' }, 503))
    .mockResolvedValueOnce(json({ programmes: [programme] }));
  vi.stubGlobal('fetch', fetchMock);
  mount();
  expect(await screen.findByRole('heading', { name: 'Catalogue setup required' })).toBeVisible();
  expect(screen.getByRole('link', { name: 'Open interactive preview' })).toHaveAttribute('href', '/users/programme-structure/preview');
  expect(screen.getByRole('link', { name: 'Open interactive preview' })).toHaveAccessibleDescription(/temporary data/);
  expect(screen.queryByRole('button', { name: 'Add programme' })).not.toBeInTheDocument();
  expect(screen.queryByText(/No programmes to show/)).not.toBeInTheDocument();
  expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([base + 'programmes/']);
  await userEvent.click(screen.getByRole('button', { name: 'Check again' }));
  expect(await screen.findByRole('button', { name: programme.name })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Add programme' })).toBeEnabled();
  expect(screen.getByRole('searchbox', { name: 'Search programmes' })).toBeVisible();
  expect(screen.queryByRole('link', { name: /legacy|preview/i })).not.toBeInTheDocument();
  expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([base + 'programmes/', base + 'programmes/']);
});

it.each([
  ['a missing API route', () => new Response('<h1>Not found</h1>', { status: 404 }), /Programme Catalogue API returned 404/],
  ['a genuine database failure', () => json({ error: 'The catalogue database request failed.', code: 'catalog_database_error' }, 503), /database request failed/],
  ['an invalid success response', () => json({ ok: true }), /did not contain a programme list/],
] as const)('surfaces %s without rendering an empty catalogue', async (_scenario, response, message) => {
  const fetchMock = vi.fn().mockResolvedValue(response());
  vi.stubGlobal('fetch', fetchMock);
  mount();
  expect(await screen.findByRole('alert')).toHaveTextContent(message);
  expect(screen.queryByRole('heading', { name: 'Catalogue setup required' })).not.toBeInTheDocument();
  expect(screen.queryByRole('link', { name: 'Open interactive preview' })).not.toBeInTheDocument();
  expect(screen.queryByText(/No programmes to show/)).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Add programme' })).not.toBeInTheDocument();
  expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([base + 'programmes/']);
});

it('warns before cancelling or leaving a changed form, but not a pristine form', async () => {
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
  try {
    const user = userEvent.setup();
    render(<MemoryRouter><Link to="/users">Users</Link><ProgrammeCatalogPage preview /></MemoryRouter>);
    const add = () => user.click(screen.getByRole('button', { name: 'Add programme' }));
    await add();
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(confirm).not.toHaveBeenCalled();
    await add();
    await user.type(screen.getByLabelText('Programme name'), 'Unsaved programme');
    const unload = new Event('beforeunload', { cancelable: true });
    expect(window.dispatchEvent(unload)).toBe(false);
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(confirm).toHaveBeenCalledWith('Discard your unsaved changes?');
    expect(screen.getByLabelText('Programme name')).toHaveValue('Unsaved programme');
    await user.click(screen.getByRole('link', { name: 'Users' }));
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(screen.getByRole('form', { name: 'Add programme' })).toBeVisible();
    confirm.mockReturnValue(true);
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('form')).not.toBeInTheDocument();
    expect(window.dispatchEvent(new Event('beforeunload', { cancelable: true }))).toBe(true);
  } finally {
    confirm.mockRestore();
  }
});

it('opens a direct group link, guards a dirty browser Back and restores the programme tab', async () => {
  const get = vi.fn(async (url: string) => {
    if (url === base + 'staff/') return json({ coaches: [], tutors: [] });
    if (url === base + 'programmes/') return json({ programmes: [programme] });
    if (url === base + `programmes/${programme.id}/`) return json(detail);
    throw new Error(`Unexpected request: ${url}`);
  });
  vi.stubGlobal('fetch', get);
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
  function HistoryControls() {
    const navigate = useNavigate();
    return <>
      <button onClick={() => navigate(-1)}>History back</button>
      <button onClick={() => navigate(1)}>History forward</button>
    </>;
  }
  const user = userEvent.setup();
  const url = `/?programme=${programme.id}&tab=overview&cohort=first&group=first-group`;
  try {
    render(<MemoryRouter initialEntries={[url]}><HistoryControls /><ProgrammeCatalogPage /></MemoryRouter>);
    expect(await screen.findByRole('heading', { name: 'First group' })).toBeVisible();
    const breadcrumbs = within(screen.getByRole('navigation', { name: 'Breadcrumb' }));
    expect(breadcrumbs.getByRole('link', { name: programme.name })).toHaveAttribute('href', `/?programme=${programme.id}&tab=overview`);
    expect(breadcrumbs.getByText('First group')).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('button', { name: 'Add module' })).toBeEnabled();
    await user.click(screen.getByRole('button', { name: 'Edit First group' }));
    await user.type(screen.getByLabelText('Group name'), ' draft');
    await user.click(screen.getByRole('button', { name: 'History back' }));
    expect(confirm).toHaveBeenCalledOnce();
    expect(screen.getByLabelText('Group name')).toHaveValue('First group draft');
    confirm.mockReturnValue(true);
    await user.click(screen.getByRole('button', { name: 'History back' }));
    expect(screen.getByRole('heading', { name: 'First group' })).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'History forward' }));
    expect(screen.getByLabelText('Group name')).toHaveValue('First group');
    await user.click(screen.getByRole('button', { name: 'Back to First group' }));
    await user.click(screen.getByRole('button', { name: 'Back to First cohort' }));
    expect(screen.getByRole('heading', { name: 'First cohort' })).toBeVisible();
    await user.click(within(screen.getByRole('navigation', { name: 'Breadcrumb' })).getByRole('link', { name: programme.name }));
    expect(screen.getByRole('button', { name: 'Overview' })).toHaveAttribute('aria-current', 'page');
    expect(get.mock.calls.filter(([path]) => path === base + `programmes/${programme.id}/`)).toHaveLength(1);
  } finally {
    confirm.mockRestore();
  }
});

it('uses the revision from the open editor and prevents retries after a conflict', async () => {
  let revisionHeader: string | null = null;
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url === base + 'staff/') return json({ coaches: [], tutors: [] });
    if (url === base + 'programmes/') return json({ programmes: [programme] });
    if (url.endsWith('/pricing/')) return json({ pricing });
    if (init?.method === 'PATCH') {
      revisionHeader = new Headers(init.headers).get('If-Match');
      return json({ error: 'Another change was saved.', code: 'catalog_revision_conflict' }, 409);
    }
    return json(detail);
  }));
  const user = userEvent.setup();
  mount();
  await user.click(await screen.findByRole('button', { name: 'Fixture programme' }));
  await user.click(await screen.findByRole('button', { name: 'Edit Fixture programme' }));
  const input = screen.getByLabelText('Programme name');
  await user.type(input, ' locally');
  await user.click(screen.getByRole('button', { name: 'Update programme' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Another change was saved.');
  expect(revisionHeader).toBe('"1"');
  expect(input).toHaveValue('Fixture programme locally');
  expect(screen.getByRole('button', { name: 'Update programme' })).toBeDisabled();
  await user.click(screen.getByRole('button', { name: 'Reload latest and discard edits' }));
  expect(screen.queryByRole('form')).not.toBeInTheDocument();
});

it('starts empty and saves a real programme; validation retains the form', async () => {
  let attempts = 0;
  let submitted: object | undefined;
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url === base + 'staff/') return json({ coaches: [], tutors: [] });
    if (url === base + 'programmes/' && init?.method === 'POST') {
      submitted = JSON.parse(String(init.body));
      attempts += 1;
      return attempts === 1 ? json({ fields: { code: ['Already exists.'] } }, 400) : json(programme, 201);
    }
    if (url === base + 'programmes/') return json({ programmes: attempts < 2 ? [] : [programme] });
    if (url.endsWith('/pricing/')) return json({ pricing });
    return json(detail);
  }));
  const user = userEvent.setup();
  mount();
  expect(await screen.findByText(/No programmes to show/)).toBeVisible();
  expect(screen.getByRole('searchbox', { name: 'Search programmes' })).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Add programme' }));
  const form = within(screen.getByRole('form', { name: 'Add programme' }));
  await user.type(form.getByLabelText('Programme name'), 'Fixture programme');
  await user.type(form.getByLabelText('Programme code'), 'TEST');
  await user.selectOptions(form.getByLabelText('Level'), '4');
  await user.type(form.getByLabelText('Total per apprentice (GBP)'), '100.00');
  await user.type(form.getByLabelText('EPA cost (GBP)'), '20.00');
  await user.click(form.getByRole('button', { name: 'Create programme' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('code: Already exists.');
  expect(form.getByLabelText('Programme name')).toHaveValue('Fixture programme');
  await user.click(form.getByRole('button', { name: 'Create programme' }));
  expect(await screen.findByText('Programme saved.')).toBeVisible();
  expect(await screen.findByRole('heading', { name: 'Fixture programme' })).toBeVisible();
  expect(submitted).toMatchObject({ name: 'Fixture programme', lecture_hours: '2.50', funding_band_maximum: null });
  expect(submitted).not.toHaveProperty('duration_months');
});

it('filters the unified catalogue and presents delivery counts returned by the API', async () => {
  const active = { ...programme, name: 'Active programme', status: 'active', counts: { cohorts: 2, groups: 3, scheduled_modules: 4 } };
  const draft = { ...programme, id: 'draft', name: 'Draft programme' };
  const archived = { ...programme, id: 'archived', name: 'Archived programme', status: 'archived', archived_at: '2027-01-01' };
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ programmes: [active, draft, archived] })));
  const user = userEvent.setup();
  mount();
  const row = (name: string) => screen.queryByRole('button', { name });
  expect(await screen.findByRole('button', { name: active.name })).toBeVisible();
  expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);
  expect(screen.getByRole('heading', { level: 1, name: 'Programme Structure' })).toBeVisible();
  expect(screen.getByText('4 scheduled modules')).toBeVisible();
  expect(row(archived.name)).toBeVisible();
  const filters = within(screen.getByRole('group', { name: 'Programme status' }));
  await user.click(filters.getByRole('button', { name: /^active$/i }));
  expect(row(active.name)).toBeVisible();
  expect(row(draft.name)).not.toBeInTheDocument();
  await user.type(screen.getByRole('searchbox', { name: 'Search programmes' }), 'draft');
  expect(row(active.name)).not.toBeInTheDocument();
  await user.click(filters.getByRole('button', { name: /^draft$/i }));
  expect(row(draft.name)).toBeVisible();
  expect(screen.queryByText('0 scheduled modules')).not.toBeInTheDocument();
  await user.clear(screen.getByRole('searchbox', { name: 'Search programmes' }));
  await user.click(filters.getByRole('button', { name: /^archived$/i }));
  expect(row(archived.name)).toBeVisible();
  expect(row(draft.name)).not.toBeInTheDocument();
});

it('filters groups by cohort and scopes pricing to the selected group', async () => {
  const fetchMock = vi.fn(async (url: string) => {
    if (url === base + 'staff/') return json({ coaches: [], tutors: [] });
    if (url === base + 'programmes/') return json({ programmes: [programme] });
    if (url.includes('/pricing/')) return json({ pricing });
    return json(detail);
  });
  vi.stubGlobal('fetch', fetchMock);
  const user = userEvent.setup();
  mount();
  await user.click(await screen.findByRole('button', { name: 'Fixture programme' }));
  await user.click(await screen.findByRole('button', { name: 'Pricing breakdown' }));
  await user.selectOptions(await screen.findByRole('combobox', { name: 'Cohort' }), 'first');
  const group = screen.getByRole('combobox', { name: 'Group' });
  expect(within(group).queryByRole('option', { name: 'Second group' })).not.toBeInTheDocument();
  await user.selectOptions(group, 'first-group');
  expect(await screen.findByText(/Based on one apprentice in First group/)).toBeVisible();
  expect(fetchMock.mock.calls.some(([url]) => url.endsWith('pricing/?group_id=first-group'))).toBe(true);
  await user.selectOptions(screen.getByRole('combobox', { name: 'Cohort' }), 'second');
  expect(screen.getByRole('combobox', { name: 'Group' })).toHaveValue('');
  expect(screen.queryByRole('option', { name: 'First group' })).not.toBeInTheDocument();
});

it('saves structured weekly topics and multiple KSB references on a curriculum module', async () => {
  let submitted: object | undefined;
  const withKsbs = { ...detail, ksbs: [
    { ...metadata, id: 'knowledge', code: 'K1', description: 'Knowledge criteria' },
    { ...metadata, id: 'skill', code: 'S1', description: 'Skill criteria' },
  ] };
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url === base + 'staff/') return json({ coaches: [], tutors: [] });
    if (url === base + 'programmes/') return json({ programmes: [programme] });
    if (url.endsWith('/pricing/')) return json({ pricing });
    if (url.endsWith('/modules/') && init?.method === 'POST') {
      submitted = JSON.parse(String(init.body));
      return json({ id: 'module' }, 201);
    }
    return json(withKsbs);
  }));
  const user = userEvent.setup();
  mount();
  await user.click(await screen.findByRole('button', { name: 'Fixture programme' }));
  await user.click(await screen.findByRole('button', { name: 'Curriculum' }));
  await user.click(await screen.findByRole('button', { name: 'Add module template' }));
  const form = within(screen.getByRole('form', { name: 'Add module template' }));
  await user.type(form.getByLabelText('Module name'), 'Reusable content');
  await user.type(form.getByLabelText('Number of weeks'), '2');
  await user.click(form.getByRole('checkbox', { name: /K1/ }));
  await user.click(form.getByRole('checkbox', { name: /S1/ }));
  await user.click(form.getByRole('button', { name: 'Add weekly topic' }));
  await user.type(form.getByLabelText('Topic content'), 'Introduction');
  await user.click(form.getByRole('button', { name: 'Create module template' }));
  expect(await screen.findByText('Module template saved.')).toBeVisible();
  expect(submitted).toMatchObject({
    name: 'Reusable content', ksb_ids: ['knowledge', 'skill'],
    weekly_topics: [{ week: 1, content: 'Introduction' }], books: null,
  });
});

it('saves only KSB links with the displayed revision and handles a stale assignment', async () => {
  const ksbs = [
    { ...metadata, id: 'knowledge', code: 'K1', description: 'Understand the process' },
    { ...metadata, id: 'skill', code: 'S1', description: 'Apply the process' },
  ];
  const module = { ...metadata, id: 'module', name: 'Planning', weeks: 1, lectures: 1, reviews: null, books: null,
    current_revision: { id: 'pin-one', number: 1 }, weekly_topics: [], ksb_ids: ['knowledge'] };
  const scope = base + `programmes/${programme.id}/`;
  const patches = vi.fn();
  vi.stubGlobal('fetch', vi.fn(async (url: string, request?: RequestInit) => {
    if (url === base + 'staff/') return json({ coaches: [], tutors: [] });
    if (url === base + 'programmes/') return json({ programmes: [programme] });
    if (url === scope) return json({ ...detail, ksbs, modules: [module] });
    if (url === scope + 'modules/module/' && request?.method === 'PATCH') {
      const expected = new Headers(request.headers).get('If-Match');
      const data = JSON.parse(String(request.body));
      patches(expected, data);
      if (expected === '"1"') {
        Object.assign(module, { revision: 2, current_revision: { id: 'pin-two', number: 2 } });
        return json({ error: 'Another change was saved. Reload the latest module.', code: 'catalog_revision_conflict' }, 409);
      }
      Object.assign(module, { ...data, revision: 3, current_revision: { id: 'pin-three', number: 3 } });
      return json(module);
    }
    throw new Error(`Unexpected request: ${url}`);
  }));
  const user = userEvent.setup();
  render(<MemoryRouter initialEntries={[`/?programme=${programme.id}&tab=curriculum&curriculum=mapping`]}>
    <ProgrammeCatalogPage />
  </MemoryRouter>);
  await user.click(await screen.findByRole('button', { name: 'Edit assignments' }));
  const pickSkill = () => user.click(screen.getByRole('checkbox', { name: /S1/ }));
  await pickSkill();
  await user.click(screen.getByRole('button', { name: 'Save assignments' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Another change was saved');
  expect(patches).toHaveBeenCalledExactlyOnceWith('"1"', { ksb_ids: ['knowledge', 'skill'] });
  expect(within(screen.getByRole('row', { name: /S1/ })).getByText('Unassigned')).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'Reload latest' }));
  await user.click(await screen.findByRole('button', { name: 'Edit assignments' }));
  expect(screen.getByRole('checkbox', { name: /K1/ })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: /S1/ })).not.toBeChecked();
  await pickSkill();
  await user.click(screen.getByRole('button', { name: 'Save assignments' }));
  expect(await screen.findByText('KSB assignments saved.')).toBeVisible();
  expect(patches).toHaveBeenLastCalledWith('"2"', { ksb_ids: ['knowledge', 'skill'] });
  expect(await screen.findByRole('columnheader', { name: /Planning\s*v3/ })).toBeVisible();
  expect(within(screen.getByRole('row', { name: /S1/ })).getByText('Assigned')).toBeInTheDocument();
});
