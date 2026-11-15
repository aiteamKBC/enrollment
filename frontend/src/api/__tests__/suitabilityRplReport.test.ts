import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { clearAllCachedResources } from '@/api/cachedRequest';
import { analyseSuitabilityRplEvidence, fetchSuitabilityRplIssuedDocuments, fetchSuitabilityRplSignatures, saveSuitabilityRplReport, signSuitabilityRplReport, SuitabilityRplRequestError } from '../suitabilityRplReport';

const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), {
  status,
  headers: { 'Content-Type': 'application/json' },
});

beforeEach(() => clearAllCachedResources());
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); clearAllCachedResources(); });

it('posts the advisory RPL analysis request with CSRF and returns structured recommendations', async () => {
  const fetcher = vi.fn(async (url: string, _init?: RequestInit) => url.includes('enrollment-csrf')
    ? json({ csrfToken: 'synthetic-csrf' })
    : json({
      recommendation: {
        overallOutcome: 'relevant-partial-no-exemption',
        reasoning: 'Partial evidence requires assessor review.',
        modules: [{
          moduleId: 'module-a', moduleName: 'Campaign planning', plannedHours: 70,
          wholeModuleReductionRecommended: false, evidenceSufficiency: 'partial',
          supportingEvidence: [{ evidenceId: 'evidence.skills-radar.1', detail: 'Self-assessment context only.' }], authoritativeKsbMappings: [], missingEvidence: ['Independent verification required.'],
          explanation: 'Partial evidence does not support removing the complete module.',
        }], evidenceGaps: ['PLR not recorded.'], warnings: [],
      },
      metadata: {
        provider: 'openai', model: 'gpt-5-nano', generatedAt: '2026-11-15T10:00:00Z',
        sourceSnapshotHash: 'snapshot-a', cvEvidenceVersion: { documentId: 'cv-a', container: 'approved', blobName: 'cv-a.pdf', sha256: 'a'.repeat(64), extractorVersion: 'cv-text-v1' },
      },
    }));
  vi.stubGlobal('fetch', fetcher);

  const result = await analyseSuitabilityRplEvidence('apprenticeship', '1601');
  expect(result.recommendation.overallOutcome).toBe('relevant-partial-no-exemption');
  expect(result.recommendation.modules[0].wholeModuleReductionRecommended).toBe(false);
  expect(result.recommendation.modules[0].evidenceSufficiency).toBe('partial');
  expect(result.recommendation.modules[0].supportingEvidence[0].evidenceId).toBe('evidence.skills-radar.1');
  const request = fetcher.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === 'POST');
  expect(request?.[0]).toContain('/enrolment_api/programme-suitability/apprenticeship/1601/suitability-rpl/ai/rpl-analysis/');
  expect(request?.[1]).toMatchObject({ credentials: 'include', method: 'POST' });
  expect((request?.[1] as RequestInit).headers).toMatchObject({ 'X-CSRFToken': 'synthetic-csrf' });
});

it('surfaces a blocked recommendation without weakening API errors', async () => {
  const fetcher = vi.fn(async (url: string) => url.includes('enrollment-csrf')
    ? json({ csrfToken: 'synthetic-csrf' })
    : json({ error: 'The approved CV has not been extracted.' }, 400));
  vi.stubGlobal('fetch', fetcher);
  await expect(analyseSuitabilityRplEvidence('apprenticeship', '1601')).rejects.toThrow('The approved CV has not been extracted.');
});

it('preserves gateway status so finalisation can reconcile a committed write', async () => {
  const fetcher = vi.fn(async (url: string) => url.includes('enrollment-csrf')
    ? json({ csrfToken: 'synthetic-csrf' })
    : json({ error: 'The report request failed (502).' }, 502));
  vi.stubGlobal('fetch', fetcher);
  await expect(saveSuitabilityRplReport('apprenticeship', '1601', {
    relevantPriorLearning: false, reductionJustified: false, assessorEvidenceReview: '', noReductionReason: '',
    suitabilityOutcome: 'not_assessed', finalSummary: '', suitabilityJustification: 'Suitable', suitabilityReviewed: true,
    rplJustification: 'Full programme', rplReviewed: true, cvExtractionAcknowledged: false,
    cvExtractionAcknowledgementReason: '', reductions: [], revision: 9, finalise: true,
  })).rejects.toMatchObject({ status: 502 } as Partial<SuitabilityRplRequestError>);
});

it('reads and posts signatures against the immutable issued report URL', async () => {
  const fetcher = vi.fn(async (url: string, init?: RequestInit) => url.includes('enrollment-csrf')
    ? json({ csrfToken: 'synthetic-csrf' })
    : json({ status: { issueId: 'issue-1', fullySigned: false, signingEnabled: false } }));
  vi.stubGlobal('fetch', fetcher);
  const status = await fetchSuitabilityRplSignatures('apprenticeship', '1601', 'issue-1');
  expect(status.status.issueId).toBe('issue-1');
  await signSuitabilityRplReport('apprenticeship', '1601', 'issue-1', {
    role: 'admin', name: 'Assessor', signature: 'data:image/png;base64,AA==', consent: true, consentText: 'I consent.',
  });
  const post = fetcher.mock.calls.find(([, init]) => (init as RequestInit | undefined)?.method === 'POST');
  expect(post?.[0]).toContain('/suitability-rpl/issued/issue-1/signatures/');
  expect((post?.[1] as RequestInit).headers).toMatchObject({ 'X-CSRFToken': 'synthetic-csrf' });
});

it('discovers only issued report envelopes for learner document lists', async () => {
  const fetcher = vi.fn(async (_url: string) => json({ documents: [{ issueId: 'issue-1', title: 'Suitability & RPL Report', signingEnabled: true, fullySigned: false }] }));
  vi.stubGlobal('fetch', fetcher);
  const result = await fetchSuitabilityRplIssuedDocuments('apprenticeship', '1601');
  expect(result.documents[0].title).toBe('Suitability & RPL Report');
  expect(fetcher.mock.calls[0][0]).toContain('/suitability-rpl/issued/');
});
