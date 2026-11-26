import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { CatalogLmsPanel } from '../programme-structure/CatalogLmsPanel';
import { CatalogModuleCountsEditor } from '../programme-structure/CatalogModuleCountsEditor';
import { CatalogKsbMapping } from '../programme-structure/CatalogKsbMapping';
import { CurriculumContent } from '../programme-structure/CatalogCurriculumContent';
import { catalogRequest, type CurriculumModule, type KsbMappingWeight, type Programme } from '../programme-structure/catalogApi';

vi.mock('../programme-structure/catalogApi', async importOriginal => {
  const actual = await importOriginal<typeof import('../programme-structure/catalogApi')>();
  return { ...actual, catalogRequest: vi.fn() };
});

const request = vi.mocked(catalogRequest);
const metrics = { query_count: 6, data_query_count: 4, rows_returned: 124, payload_bytes: 20088, payload_kb: 19.62, execution_ms: 32 };
const weight: KsbMappingWeight = {
  total_weight: '1060.00', mapping_count: 27, hard_count: 26, soft_count: 1, possible_count: 0,
  hard_weight: '1040.00', soft_weight: '20.00', possible_weight: '0',
  main_count: 26, secondary_count: 1, main_weight: '1040.00', secondary_weight: '20.00',
};
const preview = {
  snapshot: {
    programme: { id: 'PROG-ME-L4', name: 'Marketing Executive Level 4', code: 'PROG-ME-L4', level: 4, import_status: 'new' },
    cohorts: [{ id: 'c1', name: 'Feb 2027', start_date: '2027-01-01', training_end_date: '2028-01-31', import_status: 'new' }],
    groups: [{ id: 'g1', name: 'Group 1', import_status: 'new' }],
    modules: [{ id: 'm1', name: 'Social Media', sessions: 14, weekly_topics: Array(14), ksb_ids: Array(22), import_status: 'new' }],
    ksbs: [{ id: '130', code: 'B1', description: 'Behaviour', import_status: 'new' }],
    counts: { programmes: 1, cohorts: 1, groups: 1, modules: 1, weekly_topics: 14, ksbs: 33 },
  },
  preview_fingerprint: 'a'.repeat(64), can_import: false, link_schema_ready: false,
  conflicts: [], missing_fields: ['Apply migration 0019 before approving persistent LMS imports.'],
  metrics, performance_warnings: [],
};

afterEach(() => vi.clearAllMocks());

it('never requests LMS data on render or when merely opening the import panel', async () => {
  const user = userEvent.setup();
  render(<CatalogLmsPanel onImported={vi.fn()} onUpdated={vi.fn()} />);
  expect(request).not.toHaveBeenCalled();
  await user.click(screen.getByRole('button', { name: 'Import from LMS' }));
  expect(request).not.toHaveBeenCalled();
  expect(screen.getByText(/LMS access is read-only/)).toBeVisible();
});

it('shows bounded query metrics and blocks persistence until link migration is ready', async () => {
  request.mockResolvedValueOnce(preview);
  const user = userEvent.setup();
  render(<CatalogLmsPanel onImported={vi.fn()} onUpdated={vi.fn()} />);
  await user.click(screen.getByRole('button', { name: 'Import from LMS' }));
  await user.click(screen.getByRole('button', { name: 'Preview Import' }));
  expect(await screen.findByText('6 (4 data)')).toBeVisible();
  expect(screen.getByText('124')).toBeVisible();
  expect(screen.getByText(/Apply migration 0019/)).toBeVisible();
  expect(screen.getByRole('button', { name: 'Approve & Import' })).toBeDisabled();
  expect(request).toHaveBeenCalledTimes(1);
});

it('shows source mapping totals in import preview without treating them as percentages', async () => {
  request.mockResolvedValueOnce({ ...preview, snapshot: {
    ...preview.snapshot, modules: [{ ...preview.snapshot.modules[0], ksb_weights: { '130': weight } }],
  } });
  const user = userEvent.setup();
  render(<CatalogLmsPanel onImported={vi.fn()} onUpdated={vi.fn()} />);
  await user.click(screen.getByRole('button', { name: 'Import from LMS' }));
  await user.click(screen.getByRole('button', { name: 'Preview Import' }));
  const summary = await screen.findByText('Review module KSB mapping weights');
  await user.click(summary);
  expect(screen.getByText(/Total Mapping Weight 1060\.00/)).toBeVisible();
  expect(screen.getByText(/Hard 26 \(1040\.00\)/)).toBeVisible();
  expect(screen.getByText(/Main 26 \(1040\.00\)/)).toBeVisible();
  expect(screen.getByText(/not percentages/)).toBeVisible();
});

it('shows zero, missing historical weight, and pinned weight distinctly in the module views', () => {
  const ksb = { id: 'ksb', code: 'K5', description: 'Knowledge', revision: 1,
    source_system: 'lms', external_id: '130', archived_at: null };
  const module = { id: 'module', name: 'Social Media', revision: 1, source_system: 'lms', external_id: 'm1',
    archived_at: null, weeks: 1, lectures: 1, reviews: 1, books: 1, ksb_ids: ['ksb'], weekly_topics: [],
    current_revision: { id: 'revision', number: 1 }, ksb_weights: { ksb: { ...weight, total_weight: '0.00',
      hard_weight: '0.00', soft_weight: '0.00', main_weight: '0.00', secondary_weight: '0.00' } },
  } as CurriculumModule;
  render(<CatalogKsbMapping programmeCode="TEST" modules={[module, { ...module, id: 'older', name: 'Older module', ksb_weights: {} }]}
    ksbs={[ksb]} allKsbs={[ksb]} editable={false} onEdit={vi.fn()} />);
  const matrix = within(screen.getByRole('region', { name: 'Scrollable KSB mapping matrix' }));
  expect(matrix.getByText('0.00')).toBeVisible();
  expect(matrix.getByText('Weight not recorded')).toBeVisible();
  expect(matrix.getByText(/Hard 26/)).toBeVisible();
  render(<CurriculumContent topics={[]} ksbs={[{ id: 'ksb', code: 'K5', description: 'Knowledge', mapping_weight: weight }]} />);
  expect(screen.getByText(/Total Mapping Weight:/)).toBeVisible();
});

it('requires both module counts, accepts explicit zero, and sends integers on approval', async () => {
  request.mockResolvedValueOnce({ ...preview, can_import: true, link_schema_ready: true, missing_fields: [] });
  request.mockResolvedValueOnce({ programme_id: 'saved-programme' });
  const imported = vi.fn();
  const user = userEvent.setup();
  render(<CatalogLmsPanel onImported={imported} onUpdated={vi.fn()} />);
  await user.click(screen.getByRole('button', { name: 'Import from LMS' }));
  await user.click(screen.getByRole('button', { name: 'Preview Import' }));
  const approve = await screen.findByRole('button', { name: 'Approve & Import' });
  await user.type(screen.getByLabelText(/Total per apprentice/), '6000');
  await user.type(screen.getByLabelText(/EPA cost/), '1000');
  expect(approve).toBeDisabled();
  await user.type(screen.getByRole('spinbutton', { name: 'Progress Reviews' }), '0');
  expect(approve).toBeDisabled();
  await user.type(screen.getByRole('spinbutton', { name: 'Books' }), '2');
  expect(approve).toBeEnabled();
  await user.click(approve);
  expect(request).toHaveBeenLastCalledWith('lms/import/', expect.objectContaining({
    method: 'POST', data: expect.objectContaining({ module_counts: { m1: { reviews: 0, books: 2 } } }),
  }));
  expect(imported).toHaveBeenCalledWith('saved-programme');
});

it('edits existing LMS module counts without turning a missing value into zero', async () => {
  const module = { id: 'module', name: 'Existing LMS module', revision: 1, source_system: 'lms',
    external_id: 'm1', archived_at: null, weeks: 2, lectures: 2, reviews: null, books: 0,
    ksb_ids: [], weekly_topics: [], current_revision: { id: 'revision', number: 1 },
  } as CurriculumModule;
  const save = vi.fn();
  const user = userEvent.setup();
  render(<CatalogModuleCountsEditor module={module} busy={false} error="" onSave={save} onCancel={vi.fn()} />);
  const reviews = screen.getByRole('spinbutton', { name: 'Progress Reviews' });
  expect(reviews).toHaveValue(null);
  expect(screen.getByRole('spinbutton', { name: 'Books' })).toHaveValue(0);
  expect(screen.getByRole('button', { name: 'Save counts' })).toBeDisabled();
  await user.type(reviews, '3');
  await user.click(screen.getByRole('button', { name: 'Save counts' }));
  expect(save).toHaveBeenCalledWith({ reviews: 3, books: 0 });
});

it('checks a linked programme only after the administrator clicks', async () => {
  const programme: Programme = {
    id: '11111111-1111-1111-1111-111111111111', revision: 2, archived_at: null,
    source_system: 'lms', external_id: 'PROG-ME-L4', name: 'Marketing Executive Level 4',
    code: 'PROG-ME-L4', level: 4, description: '', status: 'draft', total_cost: '6000',
    epa_cost: '1000', epao: '', funding_band_maximum: null, lecture_hours: '2.5',
  };
  request.mockResolvedValueOnce({ preview_fingerprint: 'b'.repeat(64), has_changes: false, changes: [], metrics, performance_warnings: [] });
  const user = userEvent.setup();
  render(<CatalogLmsPanel programme={programme} onImported={vi.fn()} onUpdated={vi.fn()} />);
  expect(request).not.toHaveBeenCalled();
  await user.click(screen.getByRole('button', { name: 'LMS link & updates' }));
  expect(request).not.toHaveBeenCalled();
  await user.click(screen.getByRole('button', { name: 'Check for Updates' }));
  expect(await screen.findByText(/No Changes Found/)).toBeVisible();
  expect(request).toHaveBeenCalledWith(`programmes/${programme.id}/lms/check/`, { method: 'POST', data: {} });
});

it('shows per-KSB weight changes for review before any sync', async () => {
  const programme: Programme = {
    id: '11111111-1111-1111-1111-111111111111', revision: 2, archived_at: null,
    source_system: 'lms', external_id: 'PROG-ME-L4', name: 'Marketing Executive Level 4',
    code: 'PROG-ME-L4', level: 4, description: '', status: 'draft', total_cost: '6000',
    epa_cost: '1000', epao: '', funding_band_maximum: null, lecture_hours: '2.5',
  };
  request.mockResolvedValueOnce({ preview_fingerprint: 'b'.repeat(64), has_changes: true, metrics,
    performance_warnings: [], changes: [{ key: 'module_template:m1:module_template', state: 'modified',
      entity_type: 'module_template', source_id: 'm1',
      fields: [{ field: 'KSB 130 mapping weight', before: null, after: weight }],
    }],
  });
  const user = userEvent.setup();
  render(<CatalogLmsPanel programme={programme} onImported={vi.fn()} onUpdated={vi.fn()} />);
  await user.click(screen.getByRole('button', { name: 'LMS link & updates' }));
  await user.click(screen.getByRole('button', { name: 'Check for Updates' }));
  expect(await screen.findByText(/KSB 130 mapping weight/)).toBeVisible();
  expect(screen.getByText(/Total Mapping Weight 1060\.00/)).toBeVisible();
  expect(screen.getByText(/Not recorded/)).toBeVisible();
  expect(request).toHaveBeenCalledTimes(1);
});

it('requires explicit selection and keeps a separate group change out of a new-module approval', async () => {
  const programme: Programme = {
    id: '11111111-1111-1111-1111-111111111111', revision: 2, archived_at: null,
    source_system: 'lms', external_id: 'PROG-ME-L4', name: 'Marketing Executive Level 4',
    code: 'PROG-ME-L4', level: 4, description: '', status: 'draft', total_cost: '6000',
    epa_cost: '1000', epao: '', funding_band_maximum: null, lecture_hours: '2.5',
  };
  const newWeight = { ...weight, total_weight: '19170.00', hard_count: 274, hard_weight: '10960.00',
    soft_count: 274, soft_weight: '5480.00', possible_count: 273, possible_weight: '2730.00',
    mapping_count: 821, main_count: 274, main_weight: '10960.00', secondary_count: 274,
    secondary_weight: '5480.00', possible_classification_count: 273, possible_classification_weight: '2730.00' };
  request.mockResolvedValueOnce({ preview_fingerprint: 'b'.repeat(64), has_changes: true, metrics,
    performance_warnings: [], snapshot: { ...preview.snapshot,
      modules: [{ ...preview.snapshot.modules[0], id: 'm2', name: 'New module', ksb_weights: { '130': newWeight } }],
    }, changes: [
      { key: 'group:g1:group', state: 'modified', entity_type: 'group', source_id: 'g1',
        fields: [{ field: 'weekday', before: 5, after: 1 }] },
      { key: 'module_template:m2:module_template', state: 'new', entity_type: 'module_template', source_id: 'm2', fields: [] },
      { key: 'module_delivery:m2:module_delivery', state: 'new', entity_type: 'module_delivery', source_id: 'm2', fields: [] },
    ] }).mockResolvedValueOnce({ applied: 2 });
  const user = userEvent.setup();
  const updated = vi.fn();
  render(<CatalogLmsPanel programme={programme} onImported={vi.fn()} onUpdated={updated} />);
  await user.click(screen.getByRole('button', { name: 'LMS link & updates' }));
  await user.click(screen.getByRole('button', { name: 'Check for Updates' }));
  expect(await screen.findByText(/New module Hard weights/)).toBeVisible();
  expect(screen.getByText(/Hard 10960\.00 \(274 mappings\)/)).toBeVisible();
  const boxes = screen.getAllByRole('checkbox');
  expect(boxes).toHaveLength(3);
  boxes.forEach(box => expect(box).not.toBeChecked());
  const apply = screen.getByRole('button', { name: 'Approve & Apply Changes' });
  expect(apply).toBeDisabled();
  await user.click(boxes[1]);
  await user.click(boxes[2]);
  expect(boxes[0]).not.toBeChecked();
  await user.click(apply);
  expect(request).toHaveBeenLastCalledWith(`programmes/${programme.id}/lms/apply/`, expect.objectContaining({
    method: 'POST', data: expect.objectContaining({
      selected_keys: ['module_template:m2:module_template', 'module_delivery:m2:module_delivery'],
      archive_keys: [],
    }),
  }));
  expect(updated).toHaveBeenCalledOnce();
});

it('identifies Soft-only changes as source audit without claiming a Hard readiness change', async () => {
  const programme: Programme = {
    id: '11111111-1111-1111-1111-111111111111', revision: 2, archived_at: null,
    source_system: 'lms', external_id: 'PROG-ME-L4', name: 'Marketing Executive Level 4',
    code: 'PROG-ME-L4', level: 4, description: '', status: 'draft', total_cost: '6000',
    epa_cost: '1000', epao: '', funding_band_maximum: null, lecture_hours: '2.5',
  };
  request.mockResolvedValueOnce({ preview_fingerprint: 'b'.repeat(64), has_changes: true, metrics,
    performance_warnings: [], changes: [{ key: 'module_template:m1:module_template', state: 'modified',
      entity_type: 'module_template', source_id: 'm1', fields: [{ field: 'KSB 130 source mapping details',
        impact: 'source_audit_only', before: weight, after: { ...weight, soft_weight: '30.00', total_weight: '1070.00' } }],
    }],
  });
  const user = userEvent.setup();
  render(<CatalogLmsPanel programme={programme} onImported={vi.fn()} onUpdated={vi.fn()} />);
  await user.click(screen.getByRole('button', { name: 'LMS link & updates' }));
  await user.click(screen.getByRole('button', { name: 'Check for Updates' }));
  expect(await screen.findByText(/KSB 130 source mapping details/)).toBeVisible();
  expect(screen.getByText(/Source audit only: Soft\/Possible changes/)).toBeVisible();
  expect(screen.getByRole('button', { name: 'Approve & Apply Changes' })).toBeDisabled();
});
