// ============================================================================
// Apprenticeship Agreement — printable statutory document
//
// Reproduces the DfE "Apprenticeship Agreement" template: the particulars
// table, the two signature blocks, and the explanatory notes page. Field values
// come from our own records (see backend/learner_api/apprenticeship_agreement.py)
// — the template's example learner is not carried over.
//
// Signed by the apprentice and the employer only. The provider does not sign
// this document (note 6 on the form).
// ============================================================================
import jsPDF from 'jspdf';
import { formatHoursMinutes } from '@/lib/format';

const PAGE_W = 210;
const PAGE_H = 297;
const MARGIN_X = 18;
const MARGIN_TOP = 20;
const CONTENT_W = PAGE_W - MARGIN_X * 2;
const LABEL_W = 88;

export interface AgreementParticulars {
  apprenticeName: string;
  employerName: string;
  employerAddress: string;
  standard: string;
  startDate: string;
  endDate: string;
  practicalStartDate: string;
  practicalEndDate: string;
  /**
   * Weeks in the practical period, computed server-side and frozen onto the
   * agreement at issue. Falls back to computing it here when absent, so a
   * preview of an unissued agreement still shows the figure.
   */
  durationWeeks?: number | null;
  plannedOtjHours: number | null;
}

export interface AgreementSignature {
  name?: string;
  signature?: string;
  signedAt?: string;
}

export interface AgreementSignatures {
  apprentice?: AgreementSignature;
  employer?: AgreementSignature;
}

/**
 * jsPDF's built-in fonts are Latin-1 only — characters outside it (the template's
 * en-dashes and curly quotes) would render as blank boxes.
 */
function latin1(text: string): string {
  return String(text ?? '')
    .replace(/[–—]/g, '-')
    .replace(/[‘’]/g, "'")
    .replace(/[“”]/g, '"')
    .replace(/…/g, '...')
    .replace(/£/g, 'GBP ')
    .replace(/[Ā-\u{10FFFF}]/gu, '?');
}

/** DD/MM/YYYY, the format the statutory form uses. */
function fmtDate(iso?: string): string {
  if (!iso) return '';
  const m = String(iso).match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? `${m[3]}/${m[2]}/${m[1]}` : String(iso);
}

/**
 * Duration of the practical period, in weeks between its start and end dates,
 * to one decimal place — the form's own convention (its worked example spans
 * 22/01/2026 to 31/05/2027 and prints 70.7).
 *
 * Blank when either date is missing: an invented duration on a statutory
 * document is worse than a line an officer completes.
 */
export function weeksBetween(startIso?: string, endIso?: string): string {
  if (!startIso || !endIso) return '';
  const start = new Date(startIso);
  const end = new Date(endIso);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return '';
  const weeks = (end.getTime() - start.getTime()) / (1000 * 60 * 60 * 24 * 7);
  if (weeks < 0) return '';
  return weeks.toFixed(1);
}

const NOTES: { heading: string; body: string }[] = [
  {
    heading: '1. Why is an apprenticeship agreement required?',
    body:
      'The Apprenticeships, Skills, Children and Learning Act (ASCLA) 2009 and the Apprenticeships (Miscellaneous Provisions) Regulations 2017 (SI No. 2017/1310) require an apprenticeship agreement to be in place, for nearly all apprentices. This forms part of the employment arrangements between an apprentice and their employer; it is a contract of service (i.e. a contract of employment) and not a contract of apprenticeship. An apprenticeship agreement must be put in place when an individual starts an apprenticeship and should remain in place throughout (the agreement must be extended if the duration of the apprenticeship is extended).',
  },
  {
    heading: "2. What is a 'practical period'? How does this differ to 'the apprenticeship'?",
    body:
      'A practical period is the period in which an apprentice is expected to work and receive training under an approved English apprenticeship agreement; it must have a minimum duration of 8 months. The practical period covers the training; the full apprenticeship covers the training and final assessment (currently called end-point assessment).',
  },
  {
    heading: '3. Can an apprenticeship be completed without an apprenticeship agreement?',
    body:
      "Yes, there are some circumstances where an apprentice can complete a statutory apprenticeship without an apprenticeship agreement. Please refer to the 'alternative English apprenticeship' section of the latest apprenticeship funding rules.",
  },
  {
    heading: '4. What information is needed in an apprenticeship agreement?',
    body:
      'The apprenticeship agreement must comply with the requirements in ASCLA and the 2017 Regulations. It must: a) provide for the apprentice to work for the employer for reward, in an occupation for which an apprenticeship standard has been published by Skills England; b) provide for the apprentice to receive training in order to assist the apprentice to achieve the standard in the work done under the agreement; c) specify the apprenticeship’s practical period; and d) specify the amount of off-the-job training the apprentice is to receive. If the template overleaf is not used, the required information should be attached to / incorporated in an existing employment contract.',
  },
  {
    heading: '5. What is the definition of off-the-job training and how much needs to be included?',
    body:
      'Off-the-job training delivers new skills that are directly relevant to the apprenticeship standard referenced in the agreement. The volume of off-the-job training should be agreed with the training provider and must take into account any relevant prior learning. Further information can be found in the apprenticeship funding rules.',
  },
  {
    heading: '6. Who must sign the apprenticeship agreement?',
    body:
      'The employer and apprentice must sign and keep a copy of the apprenticeship agreement. The employer must give a copy to the training provider. The training provider, employer and apprentice must also sign a separate training plan; this builds upon the apprenticeship agreement and outlines the planned content and schedule of training to be delivered.',
  },
  {
    heading: '7. What is a break in learning?',
    body:
      'A break in learning is where an individual takes a break from their apprenticeship but plans to return to it in the future; this can be with or without a break from work. When the apprentice restarts the programme all documentation, including the apprenticeship agreement and training plan, must be reviewed and updated to account for the duration of the break.',
  },
];

/** Draws a bordered row with a label cell and a value cell. Returns the new y. */
function labelRow(doc: jsPDF, y: number, label: string, value: string, minHeight = 8): number {
  const valueLines = doc.splitTextToSize(latin1(value || ''), CONTENT_W - LABEL_W - 6);
  const height = Math.max(minHeight, valueLines.length * 5 + 3);

  doc.rect(MARGIN_X, y, LABEL_W, height);
  doc.rect(MARGIN_X + LABEL_W, y, CONTENT_W - LABEL_W, height);

  doc.setFontSize(9.5);
  const labelLines = doc.splitTextToSize(latin1(label), LABEL_W - 4);
  doc.text(labelLines, MARGIN_X + 2, y + 5.2);
  doc.text(valueLines, MARGIN_X + LABEL_W + 3, y + 5.2);
  return y + height;
}

/** Draws the 2x2 date/duration grid the form uses. Returns the new y. */
function dateGrid(doc: jsPDF, y: number, rows: [string, string, string, string][]): number {
  const cellW = CONTENT_W / 2;
  const labelW = cellW * 0.62;
  const height = 11;

  doc.setFontSize(9);
  rows.forEach(([leftLabel, leftValue, rightLabel, rightValue]) => {
    doc.rect(MARGIN_X, y, labelW, height);
    doc.rect(MARGIN_X + labelW, y, cellW - labelW, height);
    doc.rect(MARGIN_X + cellW, y, labelW, height);
    doc.rect(MARGIN_X + cellW + labelW, y, cellW - labelW, height);

    doc.text(doc.splitTextToSize(latin1(leftLabel), labelW - 3), MARGIN_X + 2, y + 4.5);
    doc.text(latin1(leftValue), MARGIN_X + labelW + 2, y + 6.5);
    doc.text(doc.splitTextToSize(latin1(rightLabel), labelW - 3), MARGIN_X + cellW + 2, y + 4.5);
    doc.text(latin1(rightValue), MARGIN_X + cellW + labelW + 2, y + 6.5);
    y += height;
  });
  return y;
}

/** A signature block: party label, the drawn mark (if any), and the date. */
function signatureRow(doc: jsPDF, y: number, party: string, sig?: AgreementSignature): number {
  const height = 26;
  const partyW = 34;
  const dateLabelW = 16;
  const dateW = 26;
  const markW = CONTENT_W - partyW - dateLabelW - dateW;

  doc.rect(MARGIN_X, y, partyW, height);
  doc.rect(MARGIN_X + partyW, y, markW, height);
  doc.rect(MARGIN_X + partyW + markW, y, dateLabelW, height);
  doc.rect(MARGIN_X + partyW + markW + dateLabelW, y, dateW, height);

  doc.setFontSize(9.5);
  doc.text(latin1(party), MARGIN_X + 2, y + height / 2 + 1);
  doc.text('Date:', MARGIN_X + partyW + markW + 2, y + height / 2 + 1);
  doc.text(fmtDate(sig?.signedAt), MARGIN_X + partyW + markW + dateLabelW + 2, y + height / 2 + 1);

  if (sig?.signature?.startsWith('data:image/')) {
    try {
      doc.addImage(sig.signature, 'PNG', MARGIN_X + partyW + 4, y + 3, 52, height - 6);
    } catch {
      // A corrupt data URL must not lose the whole document.
    }
  }
  if (sig?.name) {
    doc.setFontSize(8);
    doc.setTextColor(90);
    doc.text(latin1(sig.name), MARGIN_X + partyW + 4, y + height - 2.5);
    doc.setTextColor(0);
  }
  return y + height;
}

export function buildApprenticeshipAgreementPdf(
  particulars: AgreementParticulars,
  signatures: AgreementSignatures = {},
): jsPDF {
  const doc = new jsPDF({ unit: 'mm', format: 'a4' });
  doc.setFont('helvetica', 'normal');

  // ---- Page 1: the agreement ----
  let y = MARGIN_TOP;
  doc.setFontSize(20);
  doc.setFont('helvetica', 'bold');
  doc.text('APPRENTICESHIP AGREEMENT', MARGIN_X, y);
  y += 10;

  doc.setFontSize(9.5);
  doc.setFont('helvetica', 'normal');
  const intro = doc.splitTextToSize(
    latin1(
      'An apprenticeship agreement must be in place from the start of the apprenticeship. The agreement must be extended if the programme is extended.',
    ),
    CONTENT_W,
  );
  doc.text(intro, MARGIN_X, y);
  y += intro.length * 4.6 + 4;

  doc.text(latin1('The purpose of the apprenticeship agreement is to identify:'), MARGIN_X, y);
  y += 5.5;
  [
    'the apprenticeship standard connected to the apprenticeship;',
    'the dates during which the apprenticeship is expected to take place; and',
    'the amount of off the job training that the apprentice is to receive.',
  ].forEach((bullet) => {
    doc.text(`•  ${latin1(bullet)}`, MARGIN_X + 4, y);
    y += 5;
  });
  y += 5;

  doc.setFont('helvetica', 'bold');
  doc.text('Apprenticeship Particulars:', MARGIN_X, y);
  doc.setFont('helvetica', 'normal');
  y += 3;

  doc.setDrawColor(120);
  y = labelRow(doc, y, 'Apprentice name:', particulars.apprenticeName);
  y = labelRow(
    doc,
    y,
    'Place of work (employer name and location):',
    [particulars.employerName, particulars.employerAddress].filter(Boolean).join('\n'),
    18,
  );
  y = labelRow(doc, y, 'Apprenticeship standard, level and version:', particulars.standard);
  y += 6;

  const hours = particulars.plannedOtjHours ? formatHoursMinutes(particulars.plannedOtjHours) : '';
  y = dateGrid(doc, y, [
    [
      'Start date of apprenticeship (see note 2):',
      fmtDate(particulars.startDate),
      'End date of apprenticeship (see note 2):',
      fmtDate(particulars.endDate),
    ],
    [
      'Start date of practical period (see note 2):',
      fmtDate(particulars.practicalStartDate),
      'Estimated end date of practical period (see note 2):',
      fmtDate(particulars.practicalEndDate),
    ],
    [
      'Duration of practical period (see note 2):',
      // The value frozen at issue wins; computing it here is the fallback for
      // previewing an agreement that has not been issued yet.
      particulars.durationWeeks != null
        ? particulars.durationWeeks.toFixed(1)
        : weeksBetween(particulars.practicalStartDate, particulars.practicalEndDate),
      'Planned amount of off-the-job training (hours) (see note 5):',
      hours,
    ],
  ]);
  y += 8;

  doc.setFont('helvetica', 'bold');
  doc.setFontSize(9.5);
  doc.text('Signatories:', MARGIN_X, y);
  doc.setFont('helvetica', 'normal');
  y += 3;

  y = signatureRow(doc, y, 'Apprentice:', signatures.apprentice);
  y = signatureRow(doc, y, 'Employer:', signatures.employer);

  // ---- Page 2: the statutory notes ----
  doc.addPage();
  y = MARGIN_TOP;
  doc.setFontSize(16);
  doc.setFont('helvetica', 'bold');
  doc.text('APPRENTICESHIP AGREEMENT - NOTES', MARGIN_X, y);
  y += 10;

  NOTES.forEach((note) => {
    const body = doc.splitTextToSize(latin1(note.body), CONTENT_W);
    // Keep a heading with at least the first lines of its paragraph.
    if (y + 6 + body.length * 4.2 > PAGE_H - MARGIN_TOP) {
      doc.addPage();
      y = MARGIN_TOP;
    }
    doc.setFontSize(9.5);
    doc.setFont('helvetica', 'bold');
    doc.text(doc.splitTextToSize(latin1(note.heading), CONTENT_W), MARGIN_X, y);
    y += 5;
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(9);
    doc.text(body, MARGIN_X, y);
    y += body.length * 4.2 + 4;
  });

  return doc;
}

export function agreementFilename(learnerName: string): string {
  const safe = (learnerName || 'learner').replace(/[^A-Za-z0-9]+/g, '-').replace(/^-|-$/g, '');
  return `Apprenticeship-Agreement-${safe}.pdf`;
}

/**
 * Render a stored agreement and hand it back as a jsPDF document.
 *
 * The agreement's own frozen particulars and the marks on record are used, so
 * the file always reflects what was signed rather than current learner data.
 * Shared by the learner page and the admin board so both produce the same PDF.
 */
export function renderAgreementPdf(agreement: {
  particulars: AgreementParticulars;
  signatures: {
    apprentice: { signed: boolean; name: string; signedAt: string | null };
    employer: { signed: boolean; name: string; signedAt: string | null };
  };
  marks: { apprentice: string; employer: string };
}) {
  const { particulars, signatures, marks } = agreement;
  return buildApprenticeshipAgreementPdf(particulars, {
    apprentice: signatures.apprentice.signed
      ? {
          name: signatures.apprentice.name,
          signature: marks.apprentice,
          signedAt: signatures.apprentice.signedAt ?? undefined,
        }
      : undefined,
    employer: signatures.employer.signed
      ? {
          name: signatures.employer.name,
          signature: marks.employer,
          signedAt: signatures.employer.signedAt ?? undefined,
        }
      : undefined,
  });
}
