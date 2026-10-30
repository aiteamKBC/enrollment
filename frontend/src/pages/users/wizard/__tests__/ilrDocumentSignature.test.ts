/**
 * Extended ILR PDF — a drawn signature must fit its row and never paint over
 * the First Names / Print Name row above it.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import jsPDF from 'jspdf';
import type { EnrolmentBoard, IlrForm } from '../../types';
import { buildIlrPdf } from '../steps/ilrDocument';

const SIGNATURE = 'data:image/png;base64,AAAA';

const ILR = {
  contact: { byPost: null, byPhone: null, byEmail: null },
  nextOfKin: { fullName: '', relationship: '', email: '', phone: '', sameAddressAsLearner: null },
  eligibility: {
    employedInEngland: null, countryOfResidence: '', ukEeaNational: null, nationality: '',
    residentPrev3Years: null, yearsInUk: undefined, requiresWorkPermit: null, evidenceDescription: '', evidenceFiles: [],
  },
  employer: { organisationName: '', postcode: '', address: '', city: '', lineManagerName: '', lineManagerEmail: '', lineManagerPhone: '' },
  otherTraining: { attended12m: null, completedWhen: '' },
  circumstances: { caringResponsibilities: '', other: '', careLeaver: null },
  understanding: { programmeUnderstanding: '', careerProgression: '' },
  additionalInformation: { jobRoleRelevance: '', residenceNotForFullTimeEducation: '', ehcp: '', otherNames: '' },
  additional: { aged16to18: null, aged19to24: null },
  media: { consent: null },
  declarations: {
    plrShared: null, dfeContact: null, epaoDetails: null, kbcHoldsCerts: null, infoAccurate: null,
    over50PercentEngland: null, wageRateBand: '', knownByOtherName: null, plrAccessAware: null,
  },
  learnerSignature: { firstNames: 'Test', surname: 'Learner', date: '2026-09-27', signatureUrl: SIGNATURE },
  providerSignature: { printName: 'Provider Name', date: '2026-09-27', signatureUrl: SIGNATURE },
} as IlrForm;

const BOARD = { user: { name: 'Test Learner' }, contact: {}, programme: {} } as unknown as EnrolmentBoard;

// Instances take these from jsPDF.API, which the typings declare as {}.
const pdfApi = jsPDF.API as unknown as Pick<jsPDF, 'getImageProperties' | 'addImage'>;

let images: { y: number; w: number; h: number }[];

beforeEach(() => {
  images = [];
  // No logo: only signature images are drawn.
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
  // A tall, narrow scribble — the shape that used to reach the row above.
  vi.spyOn(pdfApi, 'getImageProperties').mockReturnValue({ width: 300, height: 200 } as never);
  vi.spyOn(pdfApi, 'addImage').mockImplementation(function (this: jsPDF, ...args: unknown[]) {
    images.push({ y: args[3] as number, w: args[4] as number, h: args[5] as number });
    return this;
  } as never);
});

/** Baseline (mm from the top) of a label, read back from the generated page. */
function labelBaseline(doc: jsPDF, label: string): number {
  const pages = (doc.internal as unknown as { pages: string[][] }).pages;
  const content = pages.map((page) => (page ?? []).join('\n')).join('\n');
  const match = new RegExp(`([\\d.]+) ([\\d.]+) Td\\s*\\(${label}\\) Tj`).exec(content);
  if (!match) throw new Error(`${label} not found in the PDF`);
  const k = doc.internal.scaleFactor;
  return doc.internal.pageSize.getHeight() - Number(match[2]) / k;
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('ILR PDF signatures', () => {
  it('keeps each signature small and clear of the name row above it', async () => {
    const doc = await buildIlrPdf(ILR, BOARD);

    expect(images).toHaveLength(2);
    const [learner, provider] = images;
    for (const image of images) {
      expect(image.h).toBeLessThanOrEqual(8);
      expect(image.w).toBeLessThanOrEqual(45);
    }
    // Each name row's rule is drawn 1.5mm below its text baseline.
    expect(learner.y).toBeGreaterThan(labelBaseline(doc, 'First Names') + 1.5);
    expect(provider.y).toBeGreaterThan(labelBaseline(doc, 'Print Name') + 1.5);
  });
});
