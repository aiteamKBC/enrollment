import { beforeEach, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { FileText } from 'lucide-react';
import { DocumentCard, Field, FieldGroup } from '../DocumentCard';

vi.mock('@/pages/users/wizard/steps/SignaturePad', () => ({ SignaturePad: ({ onCommit, onCancel }: { onCommit: (mark: string) => void; onCancel: () => void }) => <><button onClick={() => onCommit('Existing signature mark')}>Confirm signature</button><button onClick={onCancel}>Cancel signing</button></> }));
const preview = vi.fn(), download = vi.fn(), sign = vi.fn();
const unsigned = { signed: false, name: '', signedAt: null };
beforeEach(() => { vi.clearAllMocks(); Element.prototype.scrollIntoView = vi.fn(); });
function card(overrides = {}) {
  return <DocumentCard title="Training Plan" blurb="Existing description stays intact." signedBy="You, your employer and your training provider"
    issued fullySigned={false} learner={unsigned} other={unsigned} otherLabel="Employer" third={unsigned} thirdLabel="Provider"
    busy={false} onPreview={preview} onDownload={download} onSign={sign} fmtDate={date => date || ''}
    fields={<FieldGroup title="Programme details" icon={FileText}><Field label="Programme" value="Saved programme" /><Field label="Start date" value="" /><Field label="ILR planned hours" value="0 hours" hint="Existing hours note" /></FieldGroup>}
    revisions={<div>Version 1 — Original<button onClick={download}>View historical version</button></div>}
    {...overrides} />;
}

it('preserves supplied field values, zero hours, missing values, descriptions and revision content', () => {
  render(card());
  expect(screen.getByText('Existing description stays intact.')).toBeInTheDocument();
  expect(screen.getByText('Signed by: You, your employer and your training provider')).toBeInTheDocument();
  expect(screen.getByText('Saved programme')).toBeInTheDocument();
  expect(screen.getByText('0 hours')).toBeInTheDocument();
  expect(screen.getByText('Not recorded')).toBeInTheDocument();
  expect(screen.getByText('Existing hours note')).toBeInTheDocument();
  expect(screen.getByText('Version 1 — Original')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'View historical version' })).toBeInTheDocument();
});

it('retains preview/download callbacks and the existing signing confirmation', async () => {
  render(card());
  await userEvent.click(screen.getByRole('button', { name: 'Preview' }));
  await userEvent.click(screen.getByRole('button', { name: 'Download' }));
  expect(preview).toHaveBeenCalledOnce(); expect(download).toHaveBeenCalledOnce();
  await userEvent.click(screen.getByRole('button', { name: 'Sign' }));
  const panel = screen.getByRole('region', { name: 'Sign Training Plan' });
  expect(sign).not.toHaveBeenCalled();
  await userEvent.click(within(panel).getByRole('button', { name: 'Confirm signature' }));
  expect(sign).toHaveBeenCalledWith('Existing signature mark');
});

it('keeps unissued documents preview-only and preserves the original explanation', () => {
  render(card({ issued: false, revisions: null }));
  expect(screen.getByText('Not issued yet')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Preview' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /^(Download|Sign)$/ })).not.toBeInTheDocument();
  expect(screen.getByText(/Your training provider will issue this document/)).toBeInTheDocument();
  expect(screen.queryByText('Signature status')).not.toBeInTheDocument();
});

it('does not treat a learner signature as completion while the other parties remain unsigned', () => {
  render(card({ learner: { signed: true, name: 'Existing learner', signedAt: '2026-10-15' } }));
  expect(screen.getByText('Awaiting signatures')).toBeInTheDocument();
  expect(screen.getByText('signed 2026-10-15')).toBeInTheDocument();
  expect(screen.getAllByText('not signed')).toHaveLength(2);
  expect(screen.queryByRole('button', { name: 'Sign' })).not.toBeInTheDocument();
});

it('uses the supplied fully-signed status without changing any party data', () => {
  render(card({ fullySigned: true, learner: { signed: true, name: 'Saved learner', signedAt: null }, other: { signed: true, name: 'Saved employer', signedAt: null }, third: { signed: true, name: 'Saved provider', signedAt: null } }));
  expect(screen.getByText('Fully signed')).toBeInTheDocument();
  expect(screen.getAllByText('signed')).toHaveLength(3);
  expect(screen.queryByRole('button', { name: 'Sign' })).not.toBeInTheDocument();
});
