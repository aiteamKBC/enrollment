import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import { AuthContext } from '@/hooks/useAuth.helpers';
import { SkillsRadarThreshold } from '../programme-structure/SkillsRadarThreshold';
import { SkillsRadarScreeningSummary } from '../SkillsRadarScreeningSummary';

const api = vi.hoisted(() => ({ getSetting: vi.fn(), saveSetting: vi.fn(), getImpact: vi.fn() }));
vi.mock('@/features/programme-suitability/skills-radar/catalogue/api', () => ({
  getScreeningSetting: api.getSetting, saveScreeningSetting: api.saveSetting, getModuleImpact: api.getImpact,
}));

it('allows only an admin to edit the single threshold', async () => {
  api.getSetting.mockResolvedValue({ thresholdPercent: 50, revision: 0, schemaReady: true });
  api.saveSetting.mockResolvedValue({ thresholdPercent: 60, revision: 1, schemaReady: true });
  const noAdmin = render(<AuthContext.Provider value={{ isAdmin: false } as any}><SkillsRadarThreshold /></AuthContext.Provider>);
  expect(screen.queryByText('Skills Radar prior learning indicator')).toBeNull();
  expect(api.getSetting).not.toHaveBeenCalled();
  noAdmin.unmount();
  render(<AuthContext.Provider value={{ isAdmin: true } as any}><SkillsRadarThreshold /></AuthContext.Provider>);
  const input = await screen.findByRole('spinbutton', { name: /Weighted module readiness threshold/ });
  fireEvent.change(input, { target: { value: '60' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save threshold' }));
  await waitFor(() => expect(api.saveSetting).toHaveBeenCalledWith(60, 0));
  expect(await screen.findByDisplayValue('60')).toBeVisible();
});

it('shows setup required without offering persistence before migration 0020', async () => {
  api.getSetting.mockResolvedValueOnce({ thresholdPercent: 50, revision: 0, schemaReady: false });
  render(<AuthContext.Provider value={{ isAdmin: true } as any}><SkillsRadarThreshold /></AuthContext.Provider>);
  expect(await screen.findByText(/Apply the prepared migration 0020/)).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Save threshold' })).toBeNull();
});

it('ends loading after an API failure and recovers on retry', async () => {
  api.getSetting.mockRejectedValueOnce(new Error('The assessment request failed (404). Please retry.'))
    .mockResolvedValueOnce({ thresholdPercent: 50, revision: 0, schemaReady: true });
  render(<AuthContext.Provider value={{ isAdmin: true } as any}><SkillsRadarThreshold /></AuthContext.Provider>);
  expect(await screen.findByRole('alert')).toHaveTextContent('failed (404)');
  expect(screen.queryByText('Loading setting…')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Retry loading setting' }));
  expect(await screen.findByRole('spinbutton', { name: /Weighted module readiness threshold/ })).toHaveValue(50);
  expect(screen.queryByRole('alert')).toBeNull();
});

it('shows only verified complete flagged modules in the staff overview', async () => {
  const ksb = { id: 'ksb-1', code: 'K1', description: 'One', kind: 'Knowledge',
    standard: 'ST0596', standardVersion: '1.0', weight: { total_weight: '40', mapping_count: 1 } };
  api.getImpact.mockResolvedValue({ status: 'ready', screeningThresholdPercent: 50,
    savedLevels: { 'st0596-v1-0-K1': 'champion' }, modules: [
      { id: 'ready', name: 'Verified module', status: 'ready', revisionId: 'revision-ready', ksbs: [ksb] },
      { id: 'unmapped', name: 'Unmapped module', status: 'unavailable', ksbs: [] },
    ] });
  render(<MemoryRouter><SkillsRadarScreeningSummary kind="apprenticeship" learnerId="917" canReview /></MemoryRouter>);
  expect(await screen.findByText('Verified module:')).toBeVisible();
  expect(screen.getByText('100%')).toBeVisible();
  expect(screen.queryByText('Unmapped module')).toBeNull();
  expect(screen.getByRole('button', { name: 'Open Suitability & RPL review' })).toBeVisible();
  expect(api.getImpact).toHaveBeenCalledWith('apprenticeship', '917');
});
