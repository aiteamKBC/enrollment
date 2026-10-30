// ============================================================================
// Enrolment review — printable document export
//
// Renders a completed review (any of the three types) into a PDF: the learner
// information block, every answered section, and the two signature blocks.
//
// Follows the same approach as the Extended ILR export (wizard/steps/
// ilrDocument.ts): jsPDF with a label/value column layout, Yes/No shown with the
// chosen option marked, and signatures drawn as images above a ruled line.
// ============================================================================
import jsPDF from 'jspdf';
import type { ReviewFormResponse, ReviewSection } from '@/api/reviewForm';
import { LEARNER_ACKNOWLEDGEMENT, ULN_PRIVACY_FURTHER_INFO, ULN_PRIVACY_INTRO, ULN_PRIVACY_POINTS, ULN_PURPOSES } from './questions';

// ---- Page geometry (mm, A4 portrait) ----
const PAGE_W = 210;
const PAGE_H = 297;
const MARGIN_X = 16;
const MARGIN_TOP = 18;
const MARGIN_BOTTOM = 18;
const LABEL_W = 108;
const VALUE_X = MARGIN_X + LABEL_W + 4;
const VALUE_W = PAGE_W - MARGIN_X - VALUE_X;
const LINE_H = 4.4;
const ROW_GAP = 2;
const SIG_MAX_W = 60;
const SIG_MAX_H = 18;

/** Human titles for each panel, matching the on-screen headings. */
const SECTION_TITLES: Record<ReviewSection, string> = {
  ilr: 'ILR',
  extendedIlr: 'Extended ILR',
  functionalSkills: 'Functional Skills',
  fsJobRoleDiscussion: 'Functional Skills & Job Role Discussion: Learner & Employer',
  programmeStatus: 'Programme Status',
  priorLearning: 'Prior Learning',
  rplExperience: 'Recognition of Prior Learning and Experience',
  plr: 'Personal Learner Record (PLR)',
  skillsRadar: 'Skills Radar',
  healthSafetyVetting: 'Health & Safety Vetting',
  ulnPrivacyNotice: 'Unique Learner Number (ULN) Privacy Notice',
  learnerAcknowledgement: 'Learner Acknowledgement',
  comments: 'Comments',
};

/**
 * jsPDF's built-in fonts are Latin-1 only, so characters outside it (the em-dash
 * and curly quotes these forms contain) would render as replacement glyphs.
 * Same transcoding the ILR export needs.
 */
function latin1(text: string): string {
  return text
    .replace(/[–—]/g, '-')
    .replace(/[‘’]/g, "'")
    .replace(/[“”]/g, '"')
    .replace(/…/g, '...')
    .replace(/£/g, 'GBP ')
    .replace(/€/g, 'EUR ')
    .replace(/[^\p{ASCII} -ÿ]/gu, '?');
}

/** Turn a camelCase answer key into a readable label as a last resort. */
function humanise(key: string): string {
  const spaced = key.replace(/([a-z0-9])([A-Z])/g, '$1 $2').replace(/[_-]+/g, ' ');
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

type Row =
  | { kind: 'section'; title: string }
  | { kind: 'text'; label: string; value: string }
  | { kind: 'note'; text: string }
  | { kind: 'signature'; label: string; signed: string; name: string; date: string };

/**
 * Flatten the stored answers into printable rows.
 *
 * `questionLabels` maps answer keys to the exact wording shown on screen, so the
 * PDF reads like the form rather than like the JSON behind it.
 */
function buildRows(data: ReviewFormResponse, questionLabels: Record<string, string>): Row[] {
  const rows: Row[] = [];
  const info = data.learnerInformation;

  rows.push({ kind: 'section', title: 'Learner Information' });
  rows.push({ kind: 'text', label: 'Name', value: info.name });
  rows.push({ kind: 'text', label: 'Programme Name', value: info.programmeName });
  rows.push({ kind: 'text', label: 'Programme Start Date', value: info.programmeStartDate });
  rows.push({ kind: 'text', label: 'Planned End Date', value: info.plannedEndDate });
  rows.push({ kind: 'text', label: 'Programme Status', value: info.programmeStatus });
  rows.push({ kind: 'text', label: 'Employer', value: info.employer });
  rows.push({ kind: 'text', label: 'Manager', value: info.manager });
  rows.push({ kind: 'text', label: 'Mentor', value: info.mentor });

  const answers = data.answers as Record<string, unknown>;

  for (const section of data.sections) {
    const value = answers?.[section];
    rows.push({ kind: 'section', title: SECTION_TITLES[section] ?? humanise(section) });

    // Skills Radar is handled before the "not completed" check below: the
    // assessment itself comes from the learner's own record, so it must print
    // even when the reviewer has written no note.
    if (section === 'skillsRadar') {
      const radar = data.skillsRadar;
      if (!radar || radar.items.length === 0) {
        rows.push({ kind: 'note', text: 'The learner has not completed the Skills Radar self-assessment.' });
      } else {
        const label = radar.standardLabel ? `${radar.standardLabel} — ` : '';
        rows.push({ kind: 'note', text: `${label}${radar.answered} of ${radar.total} answered.` });
        let theme = '';
        for (const item of radar.items) {
          const itemTheme = item.theme || 'Other';
          if (itemTheme !== theme) {
            theme = itemTheme;
            rows.push({ kind: 'note', text: theme });
          }
          const codes = item.codes.length ? `${item.codes.join(', ')} - ` : '';
          rows.push({
            kind: 'text',
            label: `${codes}${item.title || item.ksbId}`,
            value: item.level ? `${item.level}${item.score != null ? ` (${item.score}/8)` : ''}` : 'Not answered',
          });
          if (item.note) rows.push({ kind: 'note', text: item.note });
        }
      }
      const note = (value as { notes?: string } | null)?.notes;
      if (note) rows.push({ kind: 'text', label: "Reviewer's notes", value: note });
      continue;
    }

    // The ULN panels are read-and-confirm: the wording itself belongs in the
    // document, so it prints whether or not the panel has been saved yet.
    if (section === 'ulnPrivacyNotice') {
      rows.push({ kind: 'note', text: ULN_PRIVACY_INTRO });
      ULN_PRIVACY_POINTS.forEach((point, index) => {
        rows.push({ kind: 'note', text: `${index + 1}. ${point}` });
        if (index === 1) ULN_PURPOSES.forEach((purpose, i) => rows.push({ kind: 'note', text: `   ${String.fromCharCode(97 + i)}) ${purpose}` }));
      });
      rows.push({ kind: 'note', text: ULN_PRIVACY_FURTHER_INFO });
    }
    if (section === 'learnerAcknowledgement') {
      rows.push({ kind: 'note', text: LEARNER_ACKNOWLEDGEMENT });
    }

    if (value == null || (typeof value === 'object' && Object.keys(value).length === 0)) {
      rows.push({ kind: 'note', text: 'Not completed.' });
      continue;
    }

    // Prior Learning is a list of items, each with its own fields.
    if (section === 'priorLearning') {
      const items = (value as { items?: Record<string, string>[] }).items ?? [];
      if (items.length === 0) {
        rows.push({ kind: 'note', text: 'No records available.' });
        continue;
      }
      items.forEach((item, index) => {
        rows.push({ kind: 'note', text: `Item ${index + 1}` });
        for (const [key, text] of Object.entries(item)) {
          if (text) rows.push({ kind: 'text', label: humanise(key), value: String(text) });
        }
      });
      continue;
    }

    // Functional Skills nests assessment lists, exemptions and per-subject results.
    if (section === 'functionalSkills') {
      const fs = value as {
        initialAssessments?: Record<string, string>[];
        diagnosticAssessments?: Record<string, string>[];
        exemptions?: Record<string, string>;
        results?: Record<string, { score?: string; assessmentDate?: string }>;
      };
      const lists: [string, Record<string, string>[]][] = [
        ['Functional Skills Assessments', fs.initialAssessments ?? []],
        ['Diagnostic Assessments', fs.diagnosticAssessments ?? []],
      ];
      for (const [title, list] of lists) {
        rows.push({ kind: 'note', text: title });
        if (list.length === 0) {
          rows.push({ kind: 'note', text: 'None recorded.' });
        } else {
          list.forEach((row) => {
            const parts = [row.subject, row.level, row.date, row.outcome].filter(Boolean);
            rows.push({ kind: 'text', label: row.subject || 'Assessment', value: parts.join(' | ') });
          });
        }
      }
      if (fs.exemptions && Object.values(fs.exemptions).some(Boolean)) {
        rows.push({ kind: 'note', text: 'Exemptions' });
        for (const [subject, status] of Object.entries(fs.exemptions)) {
          if (status) rows.push({ kind: 'text', label: subject, value: status });
        }
      }
      if (fs.results && Object.keys(fs.results).length > 0) {
        rows.push({ kind: 'note', text: 'Results' });
        for (const [item, result] of Object.entries(fs.results)) {
          const parts = [result?.score, result?.assessmentDate].filter(Boolean);
          if (parts.length) rows.push({ kind: 'text', label: item, value: parts.join(' | ') });
        }
      }
      continue;
    }

    // PLR keeps its own shape; everything else is a flat key/answer map.
    if (section === 'plr') {
      const plr = value as { uln?: string; reportedAttainment?: string; subjectLevels?: Record<string, string> };
      if (plr.uln) rows.push({ kind: 'text', label: 'Unique Learner Number (ULN)', value: plr.uln });
      if (plr.reportedAttainment) {
        rows.push({ kind: 'text', label: 'Highest level reported in the ILR', value: plr.reportedAttainment });
      }
      for (const [subject, level] of Object.entries(plr.subjectLevels ?? {})) {
        rows.push({ kind: 'text', label: `Calculated level - ${subject}`, value: level });
      }
      continue;
    }

    for (const [key, answer] of Object.entries(value as Record<string, unknown>)) {
      if (answer == null || answer === '') continue;
      rows.push({
        kind: 'text',
        label: questionLabels[key] ?? humanise(key),
        value: String(answer),
      });
    }
  }

  const { learner, admin, employer } = data.signatures;
  const gbDate = (iso?: string | null) => (iso ? new Date(iso).toLocaleDateString('en-GB') : '');

  rows.push({ kind: 'section', title: 'Declaration' });
  rows.push({
    kind: 'note',
    text: 'The parties below confirm the answers recorded in this review are accurate.',
  });
  rows.push({
    kind: 'signature', label: 'Learner signature',
    signed: learner.signature, name: learner.name,
    date: gbDate(learner.signedAt),
  });
  rows.push({
    kind: 'signature', label: 'Provider signature',
    signed: admin.signature, name: admin.name,
    date: gbDate(admin.signedAt),
  });
  // Only on reviews that actually call for an employer sign-off. Rendered even
  // when unsigned, so the document shows an outstanding signature line rather
  // than silently omitting a party who is required to sign.
  if (employer?.required) {
    rows.push({
      kind: 'signature', label: 'Employer signature',
      signed: employer.signature, name: employer.name,
      date: gbDate(employer.signedAt),
    });
  }

  return rows;
}

/**
 * Draw a signature sitting on the ruled line at (x, lineY).
 *
 * Scaled to fit inside SIG_MAX_W x SIG_MAX_H — capped on *both* axes, so a tall
 * narrow signature is bounded by its height and never overruns the block. The
 * caller reserves SIG_MAX_H above the line for it.
 */
function drawSignature(doc: jsPDF, signed: string, x: number, lineY: number): void {
  if (!signed) return;
  try {
    const props = doc.getImageProperties(signed);
    const ratio = props.height / props.width;
    let w = SIG_MAX_W;
    let h = w * ratio;
    if (h > SIG_MAX_H) {
      h = SIG_MAX_H;
      w = h / ratio;
    }
    // Bottom edge just above the line, whatever the height.
    doc.addImage(signed, 'PNG', x, lineY - h - 0.5, w, h);
  } catch {
    // A corrupt data URL must not abort the whole document.
    doc.setFont('times', 'italic').setFontSize(11).setTextColor(20, 20, 20);
    doc.text('Signed', x, lineY - 1);
  }
}

export function buildReviewPdf(data: ReviewFormResponse, questionLabels: Record<string, string>): jsPDF {
  const raw = new jsPDF({ unit: 'mm', format: 'a4' });
  // Transcode on the way out so no call site has to remember to. Both text() and
  // splitTextToSize() are wrapped — splitting must measure the same string that
  // is finally drawn, or wrapping is off by a character or two.
  const rawText = raw.text.bind(raw);
  const rawSplit = raw.splitTextToSize.bind(raw);
  const doc = raw;
  doc.text = (text: string | string[], x: number, y: number, opts?: unknown) =>
    rawText(Array.isArray(text) ? text.map(latin1) : latin1(String(text)), x, y, opts as never);
  doc.splitTextToSize = (text: string, size: number, opts?: unknown) =>
    rawSplit(latin1(String(text)), size, opts as never);

  let y = MARGIN_TOP;

  const newPage = () => {
    doc.addPage();
    y = MARGIN_TOP;
  };
  const room = (needed: number) => {
    if (y + needed > PAGE_H - MARGIN_BOTTOM) newPage();
  };

  // ---- Title ----
  doc.setFont('helvetica', 'bold').setFontSize(14).setTextColor(20, 20, 20);
  doc.text(data.reviewLabel || 'Enrolment review', MARGIN_X, y);
  y += 6;
  doc.setFont('helvetica', 'normal').setFontSize(9).setTextColor(90, 90, 90);
  const meta = [
    data.scheduledDate ? `Date: ${data.scheduledDate}` : '',
    data.reviewedBy ? `Reviewed by: ${data.reviewedBy}` : '',
    data.completedAt ? `Completed: ${new Date(data.completedAt).toLocaleDateString('en-GB')}` : '',
  ].filter(Boolean).join('    ');
  if (meta) {
    doc.text(meta, MARGIN_X, y);
    y += 6;
  }

  for (const row of buildRows(data, questionLabels)) {
    if (row.kind === 'section') {
      room(14);
      y += 3;
      doc.setFillColor(240, 242, 246);
      doc.rect(MARGIN_X, y - 4, PAGE_W - MARGIN_X * 2, 7, 'F');
      doc.setFont('helvetica', 'bold').setFontSize(10).setTextColor(30, 30, 30);
      const title = doc.splitTextToSize(row.title, PAGE_W - MARGIN_X * 2 - 4) as string[];
      doc.text(title[0], MARGIN_X + 2, y + 1);
      y += 9;
      continue;
    }

    if (row.kind === 'note') {
      doc.setFont('helvetica', 'italic').setFontSize(9).setTextColor(90, 90, 90);
      const lines = doc.splitTextToSize(row.text, PAGE_W - MARGIN_X * 2) as string[];
      room(lines.length * LINE_H + ROW_GAP);
      doc.text(lines, MARGIN_X, y);
      y += lines.length * LINE_H + ROW_GAP;
      continue;
    }

    if (row.kind === 'signature') {
      // Lay the block out top-down: label, then a gap tall enough for the image,
      // then the ruled line and the printed name. drawSignature draws upward from
      // the line, so the gap must be the image's full height (SIG_MAX_H) or a
      // tall signature rides back over the label above it.
      const LABEL_GAP = 3.5;      // label baseline -> top of the signature area
      const NAME_GAP = 4.5;       // ruled line -> printed name baseline
      const BLOCK_GAP = 7;        // space before the next signature block
      room(LABEL_GAP + SIG_MAX_H + NAME_GAP + BLOCK_GAP);

      doc.setFont('helvetica', 'normal').setFontSize(9).setTextColor(60, 60, 60);
      doc.text(row.label, MARGIN_X, y);

      const lineY = y + LABEL_GAP + SIG_MAX_H;
      drawSignature(doc, row.signed, MARGIN_X, lineY);
      doc.setDrawColor(150, 150, 150);
      doc.line(MARGIN_X, lineY + 1, MARGIN_X + SIG_MAX_W + 10, lineY + 1);

      doc.setFontSize(8).setTextColor(90, 90, 90);
      doc.text(row.signed ? row.name : 'Not signed', MARGIN_X, lineY + NAME_GAP);
      // Sit the date on the ruled line, clear of the signature area.
      if (row.date) doc.text(`Date: ${row.date}`, MARGIN_X + SIG_MAX_W + 20, lineY);
      y = lineY + NAME_GAP + BLOCK_GAP;
      continue;
    }

    // label / value pair
    doc.setFont('helvetica', 'normal').setFontSize(9);
    const labelLines = doc.splitTextToSize(row.label, LABEL_W) as string[];
    const valueLines = doc.splitTextToSize(row.value || '-', VALUE_W) as string[];
    const height = Math.max(labelLines.length, valueLines.length) * LINE_H + ROW_GAP;
    room(height);
    doc.setTextColor(90, 90, 90);
    doc.text(labelLines, MARGIN_X, y);
    doc.setTextColor(20, 20, 20);
    doc.text(valueLines, VALUE_X, y);
    y += height;
  }

  return doc;
}

export function reviewDocumentFilename(data: ReviewFormResponse): string {
  const name = (data.learnerInformation.name || 'learner').replace(/[^a-z0-9]+/gi, '-');
  const label = (data.reviewLabel || 'review').replace(/[^a-z0-9]+/gi, '-');
  const date = data.scheduledDate || new Date().toISOString().slice(0, 10);
  return `${label}-${name}-${date}.pdf`.replace(/-+/g, '-').toLowerCase();
}

export function downloadReviewPdf(data: ReviewFormResponse, questionLabels: Record<string, string>): void {
  buildReviewPdf(data, questionLabels).save(reviewDocumentFilename(data));
}
