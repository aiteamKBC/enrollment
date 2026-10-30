// ============================================================================
// Extended ILR — printable document export
//
// Renders the answers captured on the Extended ILR step into a PDF laid out
// like the printed OnBoarding-ILR form: a label column, an answer column, and
// Yes/No questions shown with both options visible and the chosen one marked.
// The final page carries the learning declaration and the two signature blocks.
// ============================================================================
import jsPDF from 'jspdf';
import type { EnrolmentBoard, IlrForm } from '../../types';
import { IlrText } from './ilrAdditionalText';
import { cleanTexts } from '../layout/texts';
import { plainText } from '../layout/richFormat';

// ---- Page geometry (mm, A4 portrait) ----
const PAGE_W = 210;
const PAGE_H = 297;
const MARGIN_X = 18;
const MARGIN_TOP = 41;
const MARGIN_BOTTOM = 20;
const LABEL_W = 88;
const VALUE_X = MARGIN_X + LABEL_W + 4;
const VALUE_W = PAGE_W - MARGIN_X - VALUE_X;
const LINE_H = 4.6;
const ROW_GAP = 2.2;

// Kent Business College brand colours used throughout the generated document.
const BRAND_PURPLE: [number, number, number] = [74, 28, 170];
const BRAND_PURPLE_DARK: [number, number, number] = [43, 18, 92];
const BRAND_PURPLE_LIGHT: [number, number, number] = [244, 240, 253];
const BRAND_GOLD: [number, number, number] = [211, 164, 42];
const KENT_LOGO_URL = '/assets/kbc-logo.png';

let kentLogoPromise: Promise<string | null> | null = null;

/** Load the public Kent logo once and turn it into a format jsPDF can embed. */
function loadKentLogo(): Promise<string | null> {
  if (kentLogoPromise) return kentLogoPromise;
  kentLogoPromise = fetch(KENT_LOGO_URL)
    .then((response) => {
      if (!response.ok) throw new Error('Kent logo could not be loaded');
      return response.blob();
    })
    .then((blob) => new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(blob);
    }))
    // The form must remain downloadable if a deployment is temporarily missing
    // the asset; the branded text header still identifies the document.
    .catch(() => null);
  return kentLogoPromise;
}

type Row =
  | { kind: 'section'; title: string }
  | { kind: 'note'; text: string }
  | { kind: 'text'; label: string; value: string }
  | { kind: 'yesno'; label: string; value: boolean | null }
  | { kind: 'signature'; label: string; signed?: string; date?: string };

/**
 * jsPDF's built-in fonts are Latin-1 only, so characters outside it (em-dash,
 * curly quotes, £ in option labels) render as a replacement glyph. Map the ones
 * this form actually produces to their Latin-1 equivalents.
 */
function latin1(text: string): string {
  return text
    .replace(/[–—]/g, '-')
    .replace(/[‘’]/g, "'")
    .replace(/[“”]/g, '"')
    .replace(/…/g, '...')
    .replace(/£/g, 'GBP ')
    .replace(/€/g, 'EUR ')
    // Anything still outside Latin-1 would render as a blank box. Matched with
    // the /u flag so astral characters (emoji) count as one, not two.
    .replace(/[Ā-\u{10FFFF}]/gu, '?');
}

/** DD/MM/YYYY for display; passes through anything not an ISO date. */
function fmtDate(iso?: string): string {
  if (!iso) return '';
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  return m ? `${m[3]}/${m[2]}/${m[1]}` : iso;
}

function fmtList(files: string[]): string {
  return files.length ? files.join(', ') : '';
}

/**
 * Flattens the form into the ordered rows of the printed document.
 *
 * `texts` is the wording published from the wizard builder. A question whose
 * wording was edited is printed as edited — as the learner was asked it;
 * everything else keeps the document's own wording.
 */
export function buildRows(ilr: IlrForm, board: EnrolmentBoard, texts?: Record<string, string>): Row[] {
  const rows: Row[] = [];
  const w = (slot: string, printed: string): string => {
    const edited = cleanTexts(texts)?.[slot];
    return edited ? plainText(edited) : printed;
  };

  rows.push({ kind: 'section', title: 'Learner' });
  rows.push({ kind: 'text', label: 'Name', value: board.user.name });
  rows.push({ kind: 'text', label: 'Learner reference number', value: board.user.reference });
  rows.push({ kind: 'text', label: 'Programme', value: board.programme.name });
  rows.push({ kind: 'text', label: 'Cohort', value: board.programme.cohort });

  rows.push({ kind: 'section', title: w('xilr.h.contact.title', 'Contact Preferences') });
  rows.push({ kind: 'yesno', label: w('xilr.contactPost.label', 'By post'), value: ilr.contact.byPost });
  rows.push({ kind: 'yesno', label: w('xilr.contactPhone.label', 'By phone'), value: ilr.contact.byPhone });
  rows.push({ kind: 'yesno', label: w('xilr.contactEmail.label', 'By e-mail'), value: ilr.contact.byEmail });

  rows.push({ kind: 'section', title: w('xilr.h.nextOfKin.title', 'Emergency contact details/Next of kin') });
  rows.push({ kind: 'text', label: w('xilr.nokName.label', 'Full name'), value: ilr.nextOfKin.fullName });
  rows.push({ kind: 'text', label: w('xilr.nokRelationship.label', 'Relationship to you'), value: ilr.nextOfKin.relationship });
  rows.push({ kind: 'text', label: w('xilr.nokEmail.label', 'Email address'), value: ilr.nextOfKin.email });
  rows.push({ kind: 'text', label: w('xilr.nokPhone.label', 'Phone number'), value: ilr.nextOfKin.phone });
  rows.push({ kind: 'yesno', label: w('xilr.nokSameAddress.label', 'Address same as learner?'), value: ilr.nextOfKin.sameAddressAsLearner });
  if (ilr.nextOfKin.sameAddressAsLearner === false) {
    rows.push({ kind: 'text', label: w('xilr.nokSameAddress.postcode', 'Postcode'), value: ilr.nextOfKin.postcode ?? '' });
    rows.push({ kind: 'text', label: w('xilr.nokSameAddress.address', 'Address'), value: ilr.nextOfKin.address ?? '' });
  }

  rows.push({ kind: 'section', title: w('xilr.h.eligibility.title', 'Eligibility') });
  rows.push({ kind: 'yesno', label: w('xilr.employedInEngland.label', 'Are you primarily employed in England?'), value: ilr.eligibility.employedInEngland });
  rows.push({ kind: 'text', label: w('xilr.countryOfResidence.label', 'Country of residence'), value: ilr.eligibility.countryOfResidence });
  rows.push({ kind: 'yesno', label: w('xilr.ukEeaNational.label', 'Are you a UK/EEA National?'), value: ilr.eligibility.ukEeaNational });
  rows.push({ kind: 'text', label: w('xilr.nationality.label', 'Nationality'), value: ilr.eligibility.nationality });
  rows.push({ kind: 'yesno', label: w('xilr.resident3Years.label', 'Have you been resident in the UK/EEA for the previous 3 years?'), value: ilr.eligibility.residentPrev3Years });
  rows.push({ kind: 'text', label: w('xilr.yearsInUk.label', 'How many full years have you lived in the UK?'), value: ilr.eligibility.yearsInUk != null ? String(ilr.eligibility.yearsInUk) : '' });
  rows.push({ kind: 'yesno', label: w('xilr.workPermit.label', 'Do you require a Work Permit?'), value: ilr.eligibility.requiresWorkPermit });
  rows.push({ kind: 'text', label: 'Evidence provided', value: ilr.eligibility.evidenceDescription });
  rows.push({ kind: 'text', label: 'Evidence files', value: fmtList(ilr.eligibility.evidenceFiles) });

  rows.push({ kind: 'section', title: w('xilr.h.employer.title', 'Employer Details') });
  rows.push({ kind: 'text', label: w('xilr.employerName.label', 'Organisation name'), value: ilr.employer.organisationName });
  rows.push({ kind: 'text', label: w('xilr.employerPostcode.label', 'Organisation post code'), value: ilr.employer.postcode });
  rows.push({ kind: 'text', label: w('xilr.employerAddress.label', 'Organisation address'), value: ilr.employer.address });
  rows.push({ kind: 'text', label: w('xilr.employerCity.label', 'Organisation city'), value: ilr.employer.city });
  rows.push({ kind: 'text', label: w('xilr.lineManagerName.label', 'Line Manager name'), value: ilr.employer.lineManagerName });
  rows.push({ kind: 'text', label: w('xilr.lineManagerEmail.label', 'Line Manager email'), value: ilr.employer.lineManagerEmail });
  rows.push({ kind: 'text', label: w('xilr.lineManagerPhone.label', 'Line Manager phone'), value: ilr.employer.lineManagerPhone });

  rows.push({ kind: 'section', title: w('xilr.h.otherTraining.title', 'Other training') });
  rows.push({ kind: 'yesno', label: w('xilr.otherTraining.label', 'Have you attended any other government funded training programmes in the last 12 months?'), value: ilr.otherTraining.attended12m });
  rows.push({ kind: 'text', label: w('xilr.otherTraining.when', 'When was it completed?'), value: fmtDate(ilr.otherTraining.completedWhen) });

  rows.push({ kind: 'section', title: w('xilr.h.circumstances.title', 'Personal Circumstances') });
  rows.push({ kind: 'text', label: w('xilr.caring.label', 'Do you have any caring responsibilities?'), value: ilr.circumstances.caringResponsibilities });
  rows.push({ kind: 'text', label: w('xilr.otherCircumstances.label', 'Are there any other personal circumstances you want to tell us about?'), value: ilr.circumstances.other });
  rows.push({ kind: 'yesno', label: w('xilr.careLeaver.label', 'Care leaver'), value: ilr.circumstances.careLeaver });

  rows.push({ kind: 'section', title: w('xilr.h.understanding.title', 'Programme understanding') });
  rows.push({ kind: 'text', label: w('xilr.programmeUnderstanding.label', 'What is your understanding of the programme you are applying for?'), value: ilr.understanding.programmeUnderstanding });
  rows.push({ kind: 'text', label: w('xilr.careerProgression.label', 'How will this programme help you in your career development/aspirations, and/or with your progression?'), value: ilr.understanding.careerProgression });

  // Replaced the age questions, media consent and the other declarations; those
  // answers stay on older records but are no longer printed.
  const ai = ilr.additionalInformation;
  rows.push({ kind: 'section', title: w('xilr.h.additional.title', 'Additional Information') });
  rows.push({ kind: 'text', label: w('xilr.jobRoleRelevance.label', IlrText.jobRoleRelevance), value: ai?.jobRoleRelevance ?? '' });
  rows.push({ kind: 'text', label: w('xilr.residenceNotFte.label', IlrText.residenceNotForFullTimeEducation), value: ai?.residenceNotForFullTimeEducation ?? '' });
  rows.push({ kind: 'section', title: w('xilr.ehcp.heading', 'EHCP Status') });
  rows.push({ kind: 'note', text: w('xilr.ehcp.sharing', IlrText.ehcpSharing) });
  rows.push({ kind: 'text', label: w('xilr.ehcp.label', IlrText.ehcp), value: ai?.ehcp ?? '' });
  rows.push({ kind: 'note', text: w('xilr.ehcp.use', IlrText.ehcpUse) });
  rows.push({ kind: 'yesno', label: w('xilr.over50Percent.label', IlrText.over50PercentEngland), value: ilr.declarations.over50PercentEngland });
  rows.push({ kind: 'text', label: w('xilr.wageRate.label', IlrText.wageRateBand), value: ilr.declarations.wageRateBand });
  rows.push({ kind: 'yesno', label: w('xilr.knownByOtherName.label', IlrText.knownByOtherName), value: ilr.declarations.knownByOtherName });
  if (ilr.declarations.knownByOtherName === true) {
    rows.push({ kind: 'text', label: w('xilr.knownByOtherName.otherNames', IlrText.otherNames), value: ai?.otherNames ?? '' });
  }
  rows.push({ kind: 'note', text: w('xilr.plrAccessAware.record', IlrText.plrRecord) });
  rows.push({ kind: 'note', text: w('xilr.plrAccessAware.sharing', IlrText.plrSharing) });
  rows.push({ kind: 'yesno', label: w('xilr.plrAccessAware.label', IlrText.plrAccessAware), value: ilr.declarations.plrAccessAware });

  return rows;
}

/**
 * Max drawn size of a signature image in the PDF (mm), fitted preserving aspect.
 *
 * The row above each signature line (First Names / Print Name) is 12mm higher,
 * so the image must stay well under that or it paints over the name: 8mm tall
 * leaves a clear gap below that row's rule.
 */
const SIG_MAX_W = 45;
const SIG_MAX_H = 8;

/**
 * Render a signature just above the ruled line at (x, baselineY).
 *
 * A captured signature is a PNG data URL, drawn as an image. Anything else is a
 * legacy text value from before capture existed (e.g. 'Signed digitally') and is
 * drawn as italic text so old records still print.
 */
function drawSignature(doc: jsPDF, signed: string, x: number, baselineY: number): void {
  if (!signed.startsWith('data:image/')) {
    doc.setFont('times', 'italic').setFontSize(12).setTextColor(20, 20, 20);
    doc.text(signed, x, baselineY);
    return;
  }
  try {
    const props = doc.getImageProperties(signed);
    const ratio = props.height / props.width;
    let w = SIG_MAX_W;
    let h = w * ratio;
    if (h > SIG_MAX_H) {
      h = SIG_MAX_H;
      w = h / ratio;
    }
    // Sit the image on the ruled line rather than centring on the text baseline.
    doc.addImage(signed, 'PNG', x, baselineY - h + 1, w, h);
  } catch {
    // A corrupt/unsupported data URL must not abort the whole document.
    doc.setFont('times', 'italic').setFontSize(11).setTextColor(20, 20, 20);
    doc.text('Signed', x, baselineY);
  }
}

export async function buildIlrPdf(ilr: IlrForm, board: EnrolmentBoard, texts?: Record<string, string>): Promise<jsPDF> {
  const kentLogo = await loadKentLogo();
  const raw = new jsPDF({ unit: 'mm', format: 'a4' });

  // Transcode on the way out, so no call site has to remember to. Both text()
  // and splitTextToSize() are wrapped — splitting has to measure the same
  // string that is finally drawn, or wrapping goes wrong by a character or two.
  const rawText = raw.text.bind(raw);
  const rawSplit = raw.splitTextToSize.bind(raw);
  const doc = raw as jsPDF;
  doc.text = ((txt: string | string[], x: number, yy: number, opts?: unknown) =>
    rawText(Array.isArray(txt) ? txt.map(latin1) : latin1(txt), x, yy, opts as never)) as typeof doc.text;
  doc.splitTextToSize = ((txt: string, w: number, opts?: unknown) =>
    rawSplit(latin1(txt), w, opts as never)) as typeof doc.splitTextToSize;

  let y = MARGIN_TOP;

  const drawPageHeader = () => {
    // Clean letterhead: the supplied logo already includes the college name, so
    // it sits directly on white without another card or duplicate brand label.
    doc.setFillColor(255, 255, 255);
    doc.rect(0, 0, PAGE_W, 34, 'F');
    if (kentLogo) {
      try {
        doc.addImage(kentLogo, 'PNG', MARGIN_X, 6, 44, 20);
      } catch {
        // The adjacent text remains a complete branded fallback.
      }
    }

    doc.setFont('helvetica', 'bold').setFontSize(16).setTextColor(...BRAND_PURPLE_DARK);
    doc.text('Extended ILR', PAGE_W - MARGIN_X, 14, { align: 'right' });
    doc.setFont('helvetica', 'normal').setFontSize(8.5).setTextColor(99, 91, 113);
    doc.text('Learner Details Data Capture Form', PAGE_W - MARGIN_X, 20.5, { align: 'right' });

    // The two-part rule echoes the purple and gold in the Kent crest without
    // turning the whole top of every page into a heavy colour block.
    doc.setFillColor(...BRAND_PURPLE);
    doc.rect(MARGIN_X, 32, PAGE_W - MARGIN_X * 2, 1.2, 'F');
    doc.setFillColor(...BRAND_GOLD);
    doc.rect(MARGIN_X, 32, 42, 1.2, 'F');
  };

  drawPageHeader();

  const newPage = () => {
    doc.addPage();
    drawPageHeader();
    y = MARGIN_TOP;
  };
  /** Reserves `h` mm on the current page, breaking first if it will not fit. */
  const need = (h: number) => {
    if (y + h > PAGE_H - MARGIN_BOTTOM) newPage();
  };

  for (const row of buildRows(ilr, board, texts)) {
    if (row.kind === 'section') {
      need(12);
      y += 1.5;
      doc.setFillColor(...BRAND_PURPLE_LIGHT);
      doc.roundedRect(MARGIN_X, y, PAGE_W - MARGIN_X * 2, 8, 2, 2, 'F');
      doc.setFillColor(...BRAND_GOLD);
      doc.roundedRect(MARGIN_X, y, 2.2, 8, 1, 1, 'F');
      doc.setFont('helvetica', 'bold').setFontSize(10).setTextColor(...BRAND_PURPLE_DARK);
      doc.text(row.title, MARGIN_X + 5, y + 5.3);
      y += 11;
      continue;
    }

    if (row.kind === 'note') {
      doc.setFont('helvetica', 'normal').setFontSize(8).setTextColor(86, 76, 105);
      const lines = doc.splitTextToSize(row.text, PAGE_W - MARGIN_X * 2) as string[];
      const noteH = lines.length * 3.8 + 5;
      need(noteH);
      doc.setFillColor(249, 247, 253);
      doc.roundedRect(MARGIN_X, y - 2.5, PAGE_W - MARGIN_X * 2, noteH, 1.5, 1.5, 'F');
      doc.text(lines, MARGIN_X + 3, y + 1);
      y += noteH + 1;
      continue;
    }

    if (row.kind === 'signature') {
      need(20);
      doc.setFont('helvetica', 'normal').setFontSize(9).setTextColor(90, 90, 90);
      doc.text(row.label, MARGIN_X, y + 4);
      if (row.signed) {
        drawSignature(doc, row.signed, VALUE_X, y + 4);
      }
      // Ruled line to sign on when unsigned.
      doc.setDrawColor(150, 150, 150).setLineWidth(0.25);
      doc.line(VALUE_X, y + 6, VALUE_X + 62, y + 6);
      doc.setFont('helvetica', 'normal').setFontSize(9).setTextColor(90, 90, 90);
      doc.text('Date', VALUE_X + 70, y + 4);
      if (row.date) {
        doc.setTextColor(20, 20, 20);
        doc.text(fmtDate(row.date), VALUE_X + 80, y + 4);
      }
      doc.setDrawColor(150, 150, 150);
      doc.line(VALUE_X + 79, y + 6, PAGE_W - MARGIN_X, y + 6);
      y += 14;
      continue;
    }

    // Label / value row. Yes-No prints both options with the answer marked, so
    // the unanswered case stays visible on paper rather than silently blank.
    doc.setFont('helvetica', 'normal').setFontSize(8.5).setTextColor(69, 61, 82);
    const labelLines = doc.splitTextToSize(row.label, LABEL_W) as string[];

    let valueLines: string[];
    if (row.kind === 'yesno') {
      valueLines = [row.value === true ? '[X] Yes    [ ] No' : row.value === false ? '[ ] Yes    [X] No' : '[ ] Yes    [ ] No'];
    } else {
      valueLines = row.value ? (doc.splitTextToSize(row.value, VALUE_W) as string[]) : ['-'];
    }

    const h = Math.max(labelLines.length, valueLines.length) * LINE_H + ROW_GAP;
    need(h);

    doc.setFont('helvetica', 'normal').setFontSize(8.5).setTextColor(70, 70, 70);
    doc.text(labelLines, MARGIN_X, y + 3);

    const answered = row.kind === 'yesno' ? row.value != null : Boolean(row.value);
    doc.setFont('helvetica', answered ? 'bold' : 'normal').setFontSize(8.5);
    if (answered) doc.setTextColor(...BRAND_PURPLE_DARK);
    else doc.setTextColor(160, 156, 168);
    doc.text(valueLines, VALUE_X, y + 3);

    y += h;
    doc.setDrawColor(231, 227, 238).setLineWidth(0.2);
    doc.line(MARGIN_X, y - 1, PAGE_W - MARGIN_X, y - 1);
  }

  // ---- Declaration page ----
  newPage();
  doc.setFillColor(...BRAND_PURPLE_LIGHT);
  doc.roundedRect(MARGIN_X, y - 2, PAGE_W - MARGIN_X * 2, 11, 2, 2, 'F');
  doc.setFillColor(...BRAND_GOLD);
  doc.roundedRect(MARGIN_X, y - 2, 2.2, 11, 1, 1, 'F');
  doc.setFont('helvetica', 'bold').setFontSize(13).setTextColor(...BRAND_PURPLE_DARK);
  doc.text('Learning declaration', MARGIN_X + 5, y + 5);
  y += 15;

  doc.setFont('helvetica', 'normal').setFontSize(9).setTextColor(60, 60, 60);
  for (const p of [
    'I confirm my agreement to share my Personal Learning Record (PLR) information with Kent Business College and other relevant bodies.',
    'I understand that my programme is supported and funded by the Department of Education (DfE) on behalf of the Secretary of State. I am happy to be contacted by the relevant funding authorities in connection with this programme.',
    'I confirm all the information completed in this Application for learning document is accurate.',
  ]) {
    const lines = doc.splitTextToSize(p, PAGE_W - MARGIN_X * 2) as string[];
    need(lines.length * 4.4 + 3);
    doc.text(lines, MARGIN_X, y);
    y += lines.length * 4.4 + 3;
  }
  y += 4;

  doc.setFontSize(9).setTextColor(90, 90, 90);
  doc.text('First Names', MARGIN_X, y);
  doc.setTextColor(20, 20, 20).setFont('helvetica', 'bold');
  doc.text(ilr.learnerSignature.firstNames || '', MARGIN_X + 24, y);
  doc.setFont('helvetica', 'normal').setTextColor(90, 90, 90);
  doc.text('Surname', MARGIN_X + 96, y);
  doc.setTextColor(20, 20, 20).setFont('helvetica', 'bold');
  doc.text(ilr.learnerSignature.surname || '', MARGIN_X + 118, y);
  doc.setDrawColor(150, 150, 150).setLineWidth(0.25);
  doc.line(MARGIN_X + 24, y + 1.5, MARGIN_X + 90, y + 1.5);
  doc.line(MARGIN_X + 118, y + 1.5, PAGE_W - MARGIN_X, y + 1.5);
  y += 12;

  const sigRow = (label: string, signed?: string, date?: string) => {
    need(18);
    doc.setFont('helvetica', 'normal').setFontSize(9).setTextColor(90, 90, 90);
    doc.text(label, MARGIN_X, y);
    if (signed) {
      drawSignature(doc, signed, MARGIN_X + 24, y);
    }
    doc.setDrawColor(150, 150, 150).setLineWidth(0.25);
    doc.line(MARGIN_X + 24, y + 1.5, MARGIN_X + 96, y + 1.5);
    doc.setFont('helvetica', 'normal').setFontSize(9).setTextColor(90, 90, 90);
    doc.text('Date', MARGIN_X + 104, y);
    if (date) {
      doc.setTextColor(20, 20, 20).setFont('helvetica', 'bold');
      doc.text(fmtDate(date), MARGIN_X + 116, y);
    }
    doc.setDrawColor(150, 150, 150);
    doc.line(MARGIN_X + 116, y + 1.5, PAGE_W - MARGIN_X, y + 1.5);
    y += 16;
  };

  sigRow('Signature', ilr.learnerSignature.signatureUrl, ilr.learnerSignature.date);

  y += 4;
  need(40);
  doc.setFillColor(...BRAND_PURPLE_LIGHT);
  doc.roundedRect(MARGIN_X, y - 2, PAGE_W - MARGIN_X * 2, 11, 2, 2, 'F');
  doc.setFillColor(...BRAND_GOLD);
  doc.roundedRect(MARGIN_X, y - 2, 2.2, 11, 1, 1, 'F');
  doc.setFont('helvetica', 'bold').setFontSize(12).setTextColor(...BRAND_PURPLE_DARK);
  doc.text('Provider/Sub-contractor Declaration', MARGIN_X + 5, y + 5);
  y += 15;
  doc.setFont('helvetica', 'normal').setFontSize(9).setTextColor(60, 60, 60);
  const provLines = doc.splitTextToSize(
    'I confirm I have seen evidence to verify the learners identity, immigration permission (if applicable) and relevant eligibility for this qualification/funding',
    PAGE_W - MARGIN_X * 2
  ) as string[];
  doc.text(provLines, MARGIN_X, y);
  y += provLines.length * 4.4 + 8;

  doc.setFontSize(9).setTextColor(90, 90, 90);
  doc.text('Print Name', MARGIN_X, y);
  doc.setTextColor(20, 20, 20).setFont('helvetica', 'bold');
  doc.text(ilr.providerSignature.printName || '', MARGIN_X + 24, y);
  doc.setDrawColor(150, 150, 150).setLineWidth(0.25);
  doc.line(MARGIN_X + 24, y + 1.5, MARGIN_X + 96, y + 1.5);
  y += 12;

  sigRow('Signature', ilr.providerSignature.signatureUrl, ilr.providerSignature.date);

  // Footer page numbers
  const pages = doc.getNumberOfPages();
  for (let p = 1; p <= pages; p++) {
    doc.setPage(p);
    doc.setDrawColor(...BRAND_GOLD).setLineWidth(0.35);
    doc.line(MARGIN_X, PAGE_H - 14, PAGE_W - MARGIN_X, PAGE_H - 14);
    doc.setFont('helvetica', 'normal').setFontSize(7.5).setTextColor(150, 150, 150);
    doc.text(`Kent Business College  |  ${board.user.name}  |  Extended ILR`, MARGIN_X, PAGE_H - 10);
    doc.text(`Page ${p} of ${pages}`, PAGE_W - MARGIN_X, PAGE_H - 10, { align: 'right' });
  }

  return doc;
}

/** Filename used both for the local download and the stored Azure blob. */
export function ilrDocumentFilename(board: EnrolmentBoard): string {
  const safe = (board.user.name || 'learner').replace(/[^\w-]+/g, '-');
  return `Extended-ILR-${safe}.pdf`;
}

/** `texts`: the wording published from the wizard builder (see buildRows). */
export async function downloadIlrDocument(ilr: IlrForm, board: EnrolmentBoard, texts?: Record<string, string>): Promise<void> {
  const document = await buildIlrPdf(ilr, board, texts);
  document.save(ilrDocumentFilename(board));
}

/** The same PDF as a Blob, for filing into the enrolment-docs container. */
export async function ilrDocumentBlob(ilr: IlrForm, board: EnrolmentBoard, texts?: Record<string, string>): Promise<Blob> {
  const document = await buildIlrPdf(ilr, board, texts);
  return document.output('blob');
}
