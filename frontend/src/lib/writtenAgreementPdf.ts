// ============================================================================
// Written Agreement — printable document
//
// Follows the Written Agreement template: the particulars block, the delivery
// the provider will give (off-the-job activities, English/maths, assessment),
// End Point Assessment arrangements, the costs table against the funding band,
// the complaints/queries contacts, and the signature declarations.
//
// Signed by the learner, the employer and the provider.
//
// Values come from the issued document's frozen snapshot. Cost lines have no
// source in the system, so they print with blank values for an officer to
// complete rather than invented figures.
// ============================================================================
import jsPDF from 'jspdf';
import { formatHoursMinutes } from '@/lib/format';

const PAGE_W = 210;
const PAGE_H = 297;
const MARGIN_X = 18;
const MARGIN_TOP = 20;
const MARGIN_BOTTOM = 22;
const CONTENT_W = PAGE_W - MARGIN_X * 2;
const LABEL_W = 62;

export interface WrittenAgreementParticulars {
  apprenticeName?: string;
  jobTitle?: string;
  apprenticeshipTitle?: string;
  apprenticeshipLevel?: string;
  fundingBandValue?: string;
  isStandard?: boolean;
  startDate?: string;
  plannedEndDate?: string;
  managerName?: string;
  managerJobTitle?: string;
  employer?: string;
  employerPostcode?: string;
  employerAddress?: string;
  mainProvider?: string;
  subcontracted?: boolean;
}

export interface WrittenAgreementActivity {
  method?: string;
  title?: string;
  hours?: number | null;
  module?: string;
  week?: string;
}

export interface WrittenAgreementSignature {
  name?: string;
  position?: string;
  signature?: string;
  signedAt?: string | null;
}

function latin1(text: unknown): string {
  return String(text ?? '')
    .replace(/[–—]/g, '-')
    .replace(/[‘’]/g, "'")
    .replace(/[“”]/g, '"')
    .replace(/…/g, '...')
    .replace(/£/g, 'GBP ')
    .replace(/[Ā-\u{10FFFF}]/gu, '?');
}

function fmtDate(iso?: string | null): string {
  if (!iso) return '';
  const m = String(iso).match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? `${m[3]}/${m[2]}/${m[1]}` : String(iso);
}

export function buildWrittenAgreementPdf(
  particulars: WrittenAgreementParticulars,
  delivery: { activities?: WrittenAgreementActivity[]; totalOtjHours?: number | null; offTheJobNote?: string; englishMathsNote?: string } = {},
  epa: Record<string, string> = {},
  costs: { items?: { item: string; price: number | null }[]; total?: number | null; fundingBandMaximum?: number | null; balanceDue?: number | null } = {},
  contacts: Record<string, any> = {},
  signatures: {
    learner?: WrittenAgreementSignature;
    employer?: WrittenAgreementSignature;
    provider?: WrittenAgreementSignature;
  } = {},
): jsPDF {
  const doc = new jsPDF({ unit: 'mm', format: 'a4' });
  doc.setFont('helvetica', 'normal');
  let y = MARGIN_TOP;

  const space = (needed: number) => {
    if (y + needed > PAGE_H - MARGIN_BOTTOM) {
      doc.addPage();
      y = MARGIN_TOP;
    }
  };

  const heading = (text: string, size = 13) => {
    space(14);
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(size);
    doc.text(latin1(text), MARGIN_X, y);
    doc.setFont('helvetica', 'normal');
    y += 7;
  };

  const paragraph = (text: string, size = 9) => {
    doc.setFontSize(size);
    const lines = doc.splitTextToSize(latin1(text), CONTENT_W);
    space(lines.length * 4.2 + 2);
    doc.text(lines, MARGIN_X, y);
    y += lines.length * 4.2 + 3;
  };

  const row = (label: string, value: string) => {
    doc.setFontSize(9.5);
    const labelLines = doc.splitTextToSize(latin1(label), LABEL_W - 4);
    const valueLines = doc.splitTextToSize(latin1(value || ''), CONTENT_W - LABEL_W - 4);
    const h = Math.max(labelLines.length, valueLines.length) * 4.6 + 3;
    space(h + 2);
    doc.setTextColor(110);
    doc.text(labelLines, MARGIN_X, y + 3.4);
    doc.setTextColor(0);
    doc.text(valueLines, MARGIN_X + LABEL_W, y + 3.4);
    y += h;
    doc.setDrawColor(215);
    doc.line(MARGIN_X, y, MARGIN_X + CONTENT_W, y);
  };

  const yesNo = (label: string, value: boolean | undefined) => {
    doc.setFontSize(9.5);
    const labelLines = doc.splitTextToSize(latin1(label), LABEL_W - 4);
    const h = labelLines.length * 4.6 + 3;
    space(h + 2);
    doc.setTextColor(110);
    doc.text(labelLines, MARGIN_X, y + 3.4);
    doc.setTextColor(0);
    const x = MARGIN_X + LABEL_W;
    doc.text('YES', x, y + 3.4);
    doc.rect(x + 8, y + 0.6, 3.6, 3.6);
    doc.text('NO', x + 17, y + 3.4);
    doc.rect(x + 24, y + 0.6, 3.6, 3.6);
    if (value === true) doc.text('X', x + 8.9, y + 3.5);
    if (value === false) doc.text('X', x + 24.9, y + 3.5);
    y += h;
    doc.setDrawColor(215);
    doc.line(MARGIN_X, y, MARGIN_X + CONTENT_W, y);
  };

  // ---- Title + intro ----
  heading('Written Agreement', 18);
  paragraph(
    `This is a written agreement between the Employer and ${particulars.mainProvider || 'the Main Provider'} (Main Provider). It contains the details of the End Point Assessment Organisation and any subcontractors that may be required to complement the delivery of the apprenticeship for the apprentice detailed below. This document lays out the planned delivery requirements of the apprenticeship and the areas of responsibility for each party with the relevant costs.`,
  );
  y += 2;

  // ---- Particulars ----
  row('Apprentice Name', particulars.apprenticeName ?? '');
  row('Job Title', particulars.jobTitle ?? '');
  row('Apprenticeship Title', particulars.apprenticeshipTitle ?? '');
  row('Apprenticeship Level', particulars.apprenticeshipLevel ?? '');
  row('Apprenticeship maximum funding band value', particulars.fundingBandValue ?? '');
  row('Apprenticeship Start Date', fmtDate(particulars.startDate));
  row('Apprenticeship Planned End Date', fmtDate(particulars.plannedEndDate));
  row('Manager Name', particulars.managerName ?? '');
  row('Manager Job Title', particulars.managerJobTitle ?? '');
  row('Employer', particulars.employer ?? '');
  row('Employer Postcode', particulars.employerPostcode ?? '');
  row('Employer Address', particulars.employerAddress ?? '');
  row('Main Provider', particulars.mainProvider ?? '');
  yesNo('Is any part of the Apprenticeship to be Subcontracted?', particulars.subcontracted);
  y += 6;

  // ---- Delivery ----
  heading('Delivery');
  if (delivery.offTheJobNote) paragraph(delivery.offTheJobNote);

  const activities = delivery.activities ?? [];
  doc.setFontSize(8.8);
  activities.forEach((a) => {
    const hours = a.hours != null ? ` (${formatHoursMinutes(a.hours)})` : '';
    const text = `${a.method ? `${a.method}: ` : ''}${a.title ?? ''}${hours}`;
    const lines = doc.splitTextToSize(latin1(text), CONTENT_W - 5);
    space(lines.length * 4 + 1);
    doc.text(lines, MARGIN_X + 3, y + 3);
    y += lines.length * 4 + 1.5;
  });
  if (!activities.length) paragraph('No delivery activities have been recorded.', 9);

  y += 3;
  doc.setFont('helvetica', 'bold');
  doc.setFontSize(9.5);
  space(8);
  doc.text(
    latin1(`Total planned off-the-job training: ${formatHoursMinutes(delivery.totalOtjHours ?? 0)}`),
    MARGIN_X,
    y + 3,
  );
  doc.setFont('helvetica', 'normal');
  y += 9;

  if (delivery.englishMathsNote) {
    heading('English and maths training delivered off the job', 11);
    paragraph(delivery.englishMathsNote);
  }

  // ---- EPA ----
  doc.addPage();
  y = MARGIN_TOP;
  heading('End Point Assessment Organisation');
  paragraph(
    'In accordance with data protection law and with the apprentice’s agreement, the provider will give the End Point Assessment Organisation the apprentice details required to allow the End Point Assessment, any possible retakes and certification to take place.',
  );
  row('Assessment Organisation', epa.organisation ?? '');
  row('Postcode', epa.postcode ?? '');
  row('Arrangements for end-point assessment (including retakes)', epa.arrangements ?? '');
  row('Agreed transaction for payments to the EPA Organisation', epa.paymentArrangements ?? '');
  row('Name of External Quality Assurance Organisation', epa.externalQualityAssurance ?? '');
  y += 6;

  // ---- Costs ----
  heading('Costs');
  paragraph(
    'Any prior knowledge the apprentice may have should be accounted for and costs reduced accordingly — see the Learning Plan for details.',
  );

  const priceW = 34;
  const itemW = CONTENT_W - priceW;
  doc.setFont('helvetica', 'bold');
  doc.setFontSize(8.8);
  space(8);
  doc.text('Item', MARGIN_X, y + 3.4);
  doc.text('Associated price', MARGIN_X + itemW, y + 3.4);
  doc.setFont('helvetica', 'normal');
  y += 6;
  doc.setDrawColor(120);
  doc.line(MARGIN_X, y, MARGIN_X + CONTENT_W, y);
  y += 1.5;

  const costRow = (item: string, price: number | null | undefined, bold = false) => {
    doc.setFontSize(8.8);
    if (bold) doc.setFont('helvetica', 'bold');
    const lines = doc.splitTextToSize(latin1(item), itemW - 3);
    const h = lines.length * 4 + 2.4;
    space(h + 2);
    doc.text(lines, MARGIN_X, y + 3.1);
    doc.text(price != null ? `GBP ${price}` : 'GBP', MARGIN_X + itemW, y + 3.1);
    if (bold) doc.setFont('helvetica', 'normal');
    y += h;
    doc.setDrawColor(228);
    doc.line(MARGIN_X, y, MARGIN_X + CONTENT_W, y);
  };

  (costs.items ?? []).forEach((line) => costRow(line.item, line.price));
  costRow('Total price of Apprenticeship', costs.total, true);
  costRow('Funding Band Maximum value', costs.fundingBandMaximum, true);
  costRow('Balance due', costs.balanceDue, true);
  y += 6;

  // ---- Contacts ----
  heading('Queries and complaints');
  paragraph(
    'Please refer to the provider’s complaints procedure for the correct process for registering a complaint. First point of contact details are shown below.',
  );
  ['provider', 'epao', 'dfe'].forEach((key) => {
    const c = contacts[key];
    if (!c) return;
    row('Organisation', c.organisation ?? '');
    if (c.name) row('First contact name', c.name);
    if (c.website) row('Website', c.website);
    row('E-mail address', c.email ?? '');
    row('Phone number', c.phone ?? '');
    y += 3;
  });

  // ---- Signatures ----
  doc.addPage();
  y = MARGIN_TOP;
  heading('Declaration & Signatures');

  const block = (
    title: string,
    declaration: string,
    sig: WrittenAgreementSignature | undefined,
    roleLabel: string,
  ) => {
    space(50);
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(10.5);
    doc.text(latin1(title), MARGIN_X, y);
    doc.setFont('helvetica', 'normal');
    y += 5;
    paragraph(declaration, 8.8);

    const h = 26;
    space(h + 4);
    doc.setDrawColor(120);
    doc.rect(MARGIN_X, y, CONTENT_W, h);
    doc.setFontSize(8.8);
    doc.setTextColor(110);
    doc.text('Name Printed', MARGIN_X + 2, y + 6);
    doc.text(roleLabel, MARGIN_X + 2, y + 14);
    doc.text('Date', MARGIN_X + 2, y + 22);
    doc.text('Signature', MARGIN_X + CONTENT_W / 2 + 2, y + 6);
    doc.setTextColor(0);
    doc.text(latin1(sig?.name ?? ''), MARGIN_X + 30, y + 6);
    doc.text(latin1(sig?.position ?? ''), MARGIN_X + 30, y + 14);
    doc.text(fmtDate(sig?.signedAt), MARGIN_X + 30, y + 22);

    if (sig?.signature?.startsWith('data:image/')) {
      try {
        doc.addImage(sig.signature, 'PNG', MARGIN_X + CONTENT_W / 2 + 2, y + 8, 60, 15);
      } catch {
        // A corrupt data URL must not lose the whole document.
      }
    }
    y += h + 8;
  };

  block(
    'Apprentice:',
    'I confirm my full agreement and commitment to the content contained in this document. The information contained within the document is true and correct to the best of my knowledge.',
    signatures.learner,
    'Job Role',
  );
  block(
    'Employer:',
    'I confirm my full agreement and commitment to the content and costings contained in this document, and any additional costs due (detailed above) we will pay in full to the provider. The information contained within the document is true and correct to the best of my knowledge.',
    signatures.employer,
    'Job Role',
  );
  block(
    `${particulars.mainProvider || 'Main Provider'} (Main Provider):`,
    'I confirm my full agreement and commitment to the content and costings contained in this document. The information contained within the document is true and correct to the best of my knowledge.',
    signatures.provider,
    'Job Role',
  );

  return doc;
}

export function writtenAgreementFilename(learnerName: string): string {
  const safe = (learnerName || 'learner').replace(/[^A-Za-z0-9]+/g, '-').replace(/^-|-$/g, '');
  return `Written-Agreement-${safe}.pdf`;
}

/** Render an issued Written Agreement from its record, with the marks on file. */
export function renderWrittenAgreementPdf(document: {
  particulars: WrittenAgreementParticulars;
  delivery: Record<string, any>;
  epa: Record<string, string>;
  costs: Record<string, any>;
  contacts: Record<string, any>;
  signatures: {
    learner: { signed: boolean; name: string; position: string; signedAt: string | null };
    employer: { signed: boolean; name: string; position: string; signedAt: string | null };
    provider: { signed: boolean; name: string; position: string; signedAt: string | null };
  };
  marks: { learner: string; employer: string; provider: string };
}) {
  const pick = (
    state: { signed: boolean; name: string; position: string; signedAt: string | null },
    mark: string,
  ) =>
    state.signed
      ? { name: state.name, position: state.position, signature: mark, signedAt: state.signedAt ?? undefined }
      : undefined;

  return buildWrittenAgreementPdf(
    document.particulars,
    document.delivery,
    document.epa,
    document.costs,
    document.contacts,
    {
      learner: pick(document.signatures.learner, document.marks.learner),
      employer: pick(document.signatures.employer, document.marks.employer),
      provider: pick(document.signatures.provider, document.marks.provider),
    },
  );
}
