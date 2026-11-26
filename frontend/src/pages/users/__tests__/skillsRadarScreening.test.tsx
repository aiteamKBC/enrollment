import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, expect, it, vi } from 'vitest';
import { AuthContext } from '@/hooks/useAuth.helpers';
import { SkillsRadarThreshold } from '../programme-structure/SkillsRadarThreshold';
import { SkillsRadarScreeningSummary } from '../SkillsRadarScreeningSummary';

const api = vi.hoisted(() => ({ getSetting: vi.fn(), saveSetting: vi.fn(), getImpact: vi.fn() }));
vi.mock('@/features/programme-suitability/skills-radar/catalogue/api', () => ({
  getScreeningSetting: api.getSetting, saveScreeningSetting: api.saveSetting, getModuleImpact: api.getImpact,
}));
const defaults = { minimumQualifyingRating: 5, maximumQualifyingRating: 9,
  minimumHighRatedHardKsbs: 2, revision: 0, schemaReady: true };
beforeEach(() => vi.clearAllMocks());

it('loads defaults and saves all three admin settings atomically, then reloads persisted values', async () => {
  api.getSetting.mockResolvedValueOnce(defaults).mockResolvedValueOnce({ ...defaults,
    minimumQualifyingRating: 6, maximumQualifyingRating: 8, minimumHighRatedHardKsbs: 3, revision: 1 });
  api.saveSetting.mockResolvedValue({ ...defaults, minimumQualifyingRating: 6,
    maximumQualifyingRating: 8, minimumHighRatedHardKsbs: 3, revision: 1 });
  const noAdmin = render(<AuthContext.Provider value={{ isAdmin: false } as any}><SkillsRadarThreshold /></AuthContext.Provider>);
  expect(screen.queryByText('Skills Radar prior learning indicator')).toBeNull();
  expect(api.getSetting).not.toHaveBeenCalled();
  noAdmin.unmount();
  const admin = render(<AuthContext.Provider value={{ isAdmin: true } as any}><SkillsRadarThreshold /></AuthContext.Provider>);
  const low = await screen.findByRole('spinbutton', { name: 'Minimum qualifying rating' });
  const high = screen.getByRole('spinbutton', { name: 'Maximum qualifying rating' });
  const count = screen.getByRole('spinbutton', { name: 'Minimum qualifying Hard KSBs' });
  expect([low, high, count].map(input => (input as HTMLInputElement).value)).toEqual(['5', '9', '2']);
  expect(screen.getByRole('button', { name: 'Save Changes' })).toBeDisabled();
  fireEvent.change(low, { target: { value: '6' } });
  fireEvent.change(high, { target: { value: '8' } });
  fireEvent.change(count, { target: { value: '3' } });
  expect(screen.getByText('Unsaved changes')).toBeVisible();
  expect(screen.getByText(/at least 3 distinct verified Hard-mapped KSBs are rated 6–8/)).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Save Changes' }));
  await waitFor(() => expect(api.saveSetting).toHaveBeenCalledWith({
    minimumQualifyingRating: 6, maximumQualifyingRating: 8, minimumHighRatedHardKsbs: 3,
  }, 0));
  expect(await screen.findByText(/Screening settings saved/)).toBeVisible();
  expect(screen.getByRole('button', { name: 'Save Changes' })).toBeDisabled();
  admin.unmount();
  render(<AuthContext.Provider value={{ isAdmin: true } as any}><SkillsRadarThreshold /></AuthContext.Provider>);
  expect(await screen.findByRole('spinbutton', { name: 'Minimum qualifying rating' })).toHaveValue(6);
  expect(screen.getByRole('spinbutton', { name: 'Maximum qualifying rating' })).toHaveValue(8);
  expect(screen.getByRole('spinbutton', { name: 'Minimum qualifying Hard KSBs' })).toHaveValue(3);
});

it('shows setup required without offering persistence before migrations 0021 and 0022', async () => {
  api.getSetting.mockResolvedValueOnce({ ...defaults, schemaReady: false });
  render(<AuthContext.Provider value={{ isAdmin: true } as any}><SkillsRadarThreshold /></AuthContext.Provider>);
  expect(await screen.findByText(/apply the prepared migrations 0021 and 0022/)).toBeVisible();
  expect(screen.getByRole('button', { name: 'Save Changes' })).toBeDisabled();
  expect(screen.getByRole('spinbutton', { name: 'Minimum qualifying rating' })).toBeDisabled();
});

it('ends loading after an API failure and recovers on retry', async () => {
  api.getSetting.mockRejectedValueOnce(new Error('The assessment request failed (404). Please retry.'))
    .mockResolvedValueOnce(defaults);
  render(<AuthContext.Provider value={{ isAdmin: true } as any}><SkillsRadarThreshold /></AuthContext.Provider>);
  expect(await screen.findByRole('alert')).toHaveTextContent('failed (404)');
  expect(screen.queryByText('Loading setting…')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Retry loading setting' }));
  expect(await screen.findByRole('spinbutton', { name: 'Minimum qualifying Hard KSBs' })).toHaveValue(2);
  expect(screen.queryByRole('alert')).toBeNull();
});

it('rejects invalid rating bounds and counts before sending an API write', async () => {
  api.getSetting.mockResolvedValue(defaults);
  render(<AuthContext.Provider value={{ isAdmin: true } as any}><SkillsRadarThreshold /></AuthContext.Provider>);
  const low = await screen.findByRole('spinbutton', { name: 'Minimum qualifying rating' });
  const high = screen.getByRole('spinbutton', { name: 'Maximum qualifying rating' });
  const count = screen.getByRole('spinbutton', { name: 'Minimum qualifying Hard KSBs' });
  fireEvent.change(low, { target: { value: '10' } });
  expect(screen.getByText(/Minimum qualifying rating must be/)).toBeVisible();
  fireEvent.change(low, { target: { value: '8' } });
  fireEvent.change(high, { target: { value: '6' } });
  expect(screen.getByText(/must not exceed/)).toBeVisible();
  fireEvent.change(high, { target: { value: '9' } });
  fireEvent.change(count, { target: { value: '0' } });
  expect(screen.getByText(/Hard KSBs must be/)).toBeVisible();
  expect(screen.getByRole('button', { name: 'Save Changes' })).toBeDisabled();
  expect(api.saveSetting).not.toHaveBeenCalled();
});

it('shows only verified count-screened modules with staff diagnostics in the overview', async () => {
  const ksb = { id: 'ksb-1', code: 'K1', description: 'One', kind: 'Knowledge',
    standard: 'ST0596', standardVersion: '1.0', weight: { total_weight: '40', hard_weight: '40', hard_count: 1, mapping_count: 1 } };
  api.getImpact.mockResolvedValue({ status: 'ready', ...defaults,
    savedLevels: { 'st0596-v1-0-K1': 'champion', 'st0596-v1-0-K2': 'consistently' }, modules: [
      { id: 'ready', name: 'Verified module', status: 'ready', revisionId: 'revision-ready', ksbs: [ksb,
        { ...ksb, id: 'ksb-2', code: 'K2' }] },
      { id: 'unmapped', name: 'Unmapped module', status: 'unavailable', ksbs: [] },
    ] });
  render(<MemoryRouter><SkillsRadarScreeningSummary kind="apprenticeship" learnerId="917" canReview /></MemoryRouter>);
  expect(await screen.findByText('2 qualifying KSBs')).toBeVisible();
  expect(screen.getByRole('listitem')).toHaveTextContent('Verified module:');
  expect(screen.getByText(/K1, K2/)).toBeVisible();
  expect(screen.queryByText('Unmapped module')).toBeNull();
  expect(screen.getByRole('button', { name: 'Open Suitability & RPL review' })).toBeVisible();
  expect(api.getImpact).toHaveBeenCalledWith('apprenticeship', '917');
});

it('refreshes staff screening from saved answers after the configured rule changes', async () => {
  const ksb = (id: string, code: string) => ({ id, code, description: code, kind: 'Knowledge',
    standard: 'ST0596', standardVersion: '1.0',
    weight: { total_weight: '40', hard_weight: '40', hard_count: 1, mapping_count: 1 } });
  const impact = { status: 'ready', savedLevels: { 'st0596-v1-0-K1': 'champion', 'st0596-v1-0-K2': 'consistently' },
    modules: [{ id: 'module-1', name: 'Verified module', status: 'ready', revisionId: 'revision-1',
      ksbs: [ksb('one', 'K1'), ksb('two', 'K2')] }] };
  api.getImpact.mockResolvedValueOnce({ ...impact, ...defaults }).mockResolvedValueOnce({ ...impact, ...defaults,
    minimumQualifyingRating: 9 });
  render(<MemoryRouter><SkillsRadarScreeningSummary kind="apprenticeship" learnerId="917" canReview /></MemoryRouter>);
  expect(await screen.findByText('2 qualifying KSBs')).toBeVisible();
  fireEvent(window, new Event('focus'));
  await waitFor(() => expect(api.getImpact).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(screen.queryByText('2 qualifying KSBs')).toBeNull());
});
