import { jsPDF } from 'jspdf';
import autoTable from 'jspdf-autotable';

const PAGE_WIDTH = 210;
const PAGE_HEIGHT = 297;
const MARGIN = 15;
const CONTENT_WIDTH = PAGE_WIDTH - (MARGIN * 2);
const FOOTER_Y = 283;

const PURPLE = [112, 67, 160] as const;
const PURPLE_DARK = [82, 42, 126] as const;
const PURPLE_MID = [188, 157, 216] as const;
const PURPLE_LINE = [225, 211, 238] as const;
const PURPLE_WASH = [245, 238, 250] as const;
const INK = [29, 25, 32] as const;
const MUTED = [83, 76, 88] as const;
const SECTION_NUMBERS = ['01', '02', '03', '04', '05', '06', '07', '08'] as const; // taste-skill-ignore: approved document labels

export const PROGRAMME_UNDERSTANDING_QUESTIONS = [
  'What is your understanding of the programme you are applying for?',
  'How will this programme help you in your career development/aspirations, and/or with your progression?',
] as const;

export function formatSuitabilityRplDate(input?: string) {
  const raw = String(input || '').trim();
  if (!raw) return '';
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return raw;
  return new Intl.DateTimeFormat('en-GB', {
    day: '2-digit', month: 'long', year: 'numeric', timeZone: 'Europe/London',
  }).format(parsed);
}

export const SUITABILITY_RPL_SECTION_TITLES = [
  'Learner & Programme Details',
  'Programme Understanding',
  'Programme Suitability Assessment',
  'Skills Radar Assessment',
  'CV, Qualifications & PLR Evidence',
  'Recognition of Prior Learning Assessment',
  'Final Assessment & Recommendation',
  'Declarations & Signatures',
] as const;

export interface SuitabilityRplReportData {
  status?: 'draft' | 'finalised';
  learner?: {
    fullName?: string;
    reference?: string;
    employer?: string;
    jobTitle?: string;
    responsibilities?: string;
    programme?: string;
    level?: string;
    assessor?: string;
    assessmentDate?: string;
  };
  programmeUnderstanding?: readonly [string?, string?] | Array<{ question?: string; answer?: string }>;
  programmeSuitability?: string | {
    pathway?: string;
    roleEvidence?: string;
    evidence?: string;
    outcome?: 'not_assessed' | 'suitable' | 'further_assessment';
    justification?: string;
    reviewed?: boolean;
    responses?: Array<{
      group?: string;
      question?: string;
      answers?: string[];
      selectedOptions?: Array<{ id?: string; label?: string }>;
    }>;
  };
  skillsRadar?: Array<{ label: string; score?: string | number }>;
  skillsRadarSummary?: Array<{ label: string; score?: string | number | null; answered?: number; total?: number }>;
  evidence?: {
    cv?: string;
    qualifications?: string;
    plr?: string;
  };
  rpl?: {
    relevantPriorLearning?: boolean | null;
    reductionJustified?: boolean | null;
    evidenceReview?: string;
    noReductionReason?: string;
    justification?: string;
    reviewed?: boolean;
    rows?: Array<{ area?: string; ksb?: string; evidence?: string; hours?: string | number }>;
    originalHours?: string | number;
    reductionHours?: string | number;
    adjustedHours?: string | number;
  };
  finalAssessment?: {
    outcome?: 'not_assessed' | 'suitable' | 'further_assessment';
    rationale?: string;
    reportReference?: string;
    version?: string;
    approvalDate?: string;
  };
}

export interface SuitabilityRplReportAssets {
  logoDataUrl?: string;
}

function value(input: unknown): string {
  return input == null ? '' : String(input);
}

function excerpt(input: unknown, maximum: number): string {
  const text = value(input).trim();
  return text.length > maximum ? `${text.slice(0, maximum - 1).trimEnd()}…` : text;
}

function understandingAt(data: SuitabilityRplReportData, index: number) {
  const item = data.programmeUnderstanding?.[index];
  if (item && typeof item === 'object') {
    return { question: item.question || PROGRAMME_UNDERSTANDING_QUESTIONS[index] || '', answer: item.answer || '' };
  }
  return { question: PROGRAMME_UNDERSTANDING_QUESTIONS[index] || '', answer: value(item) };
}

function suitabilityOf(data: SuitabilityRplReportData) {
  return typeof data.programmeSuitability === 'string'
    ? { pathway: '', roleEvidence: '', evidence: '', justification: data.programmeSuitability, outcome: 'not_assessed' as const, reviewed: false, responses: [] }
    : { pathway: '', roleEvidence: '', evidence: '', justification: '', outcome: 'not_assessed' as const, reviewed: false, responses: [], ...(data.programmeSuitability || {}) };
}

function pathwayLabel(value: unknown): string {
  const raw = inputToString(value).trim();
  if (!raw) return 'Not available';
  return raw.replace(/[-_]+/g, ' ').replace(/\b\w/g, (character) => character.toUpperCase());
}

function inputToString(value: unknown): string {
  return value == null ? '' : String(value);
}

function setText(doc: jsPDF, colour: readonly [number, number, number] = INK) {
  doc.setTextColor(...colour);
}

function setLine(doc: jsPDF, colour: readonly [number, number, number] = PURPLE_LINE, width = 0.35) {
  doc.setDrawColor(...colour);
  doc.setLineWidth(width);
}

function box(doc: jsPDF, x: number, y: number, width: number, height: number, fill?: readonly [number, number, number], radius = 2.5) {
  setLine(doc);
  if (fill) {
    doc.setFillColor(...fill);
    doc.roundedRect(x, y, width, height, radius, radius, 'FD');
  } else {
    doc.roundedRect(x, y, width, height, radius, radius, 'S');
  }
}

function label(doc: jsPDF, text: string, x: number, y: number, size = 7.4, colour: readonly [number, number, number] = INK) {
  doc.setFont('helvetica', 'bold');
  doc.setFontSize(size);
  setText(doc, colour);
  doc.text(text, x, y);
}

function rightLabel(doc: jsPDF, text: string, x: number, y: number, size = 7.4, colour: readonly [number, number, number] = INK) {
  doc.setFont('helvetica', 'bold');
  doc.setFontSize(size);
  setText(doc, colour);
  doc.text(text, x, y, { align: 'right' });
}

function body(doc: jsPDF, text: string, x: number, y: number, width: number, size = 8.4, colour: readonly [number, number, number] = INK, lineHeight = 1.35) {
  doc.setFont('helvetica', 'normal');
  doc.setFontSize(size);
  setText(doc, colour);
  const lines = doc.splitTextToSize(text, width);
  doc.text(lines, x, y, { lineHeightFactor: lineHeight });
  return lines.length;
}

function pill(doc: jsPDF, text: string, x: number, y: number, width: number) {
  doc.setFillColor(...PURPLE_WASH);
  doc.roundedRect(x, y, width, 6.5, 3.25, 3.25, 'F');
  label(doc, text, x + 3.2, y + 4.25, 6.6);
}

function checkbox(doc: jsPDF, x: number, y: number, text: string, checked = false, textWidth = 42) {
  setLine(doc, PURPLE_MID, 0.45);
  if (checked) {
    doc.setFillColor(...PURPLE);
    doc.roundedRect(x, y, 4.2, 4.2, 0.8, 0.8, 'FD');
    setLine(doc, [255, 255, 255], 0.55);
    doc.line(x + 1, y + 2.2, x + 1.8, y + 3.1);
    doc.line(x + 1.8, y + 3.1, x + 3.4, y + 1.1);
  } else {
    doc.roundedRect(x, y, 4.2, 4.2, 0.8, 0.8, 'S');
  }
  body(doc, text, x + 6.7, y + 3.25, textWidth, 7.6);
}

function blankLine(doc: jsPDF, x: number, y: number, width: number) {
  setLine(doc, PURPLE_LINE, 0.32);
  doc.line(x, y, x + width, y);
}

function sectionHeading(doc: jsPDF, section: number, title: string, meta: string, y: number) {
  doc.setFillColor(...PURPLE_WASH);
  doc.roundedRect(MARGIN, y - 5.4, 9.7, 8.4, 1.8, 1.8, 'F');
  label(doc, SECTION_NUMBERS[section - 1] ?? String(section), MARGIN + 2, y, 9);
  label(doc, title, MARGIN + 13.7, y + 0.25, title.length > 39 ? 10.1 : 11);
  rightLabel(doc, meta, PAGE_WIDTH - MARGIN, y, 6.6);
}

function pageFooter(doc: jsPDF, page: number, total: number, blank: boolean) {
  blankLine(doc, MARGIN, FOOTER_Y, CONTENT_WIDTH);
  body(doc, blank ? 'Kent Business College | Blank template - no learner information' : 'Kent Business College | Suitability & RPL assessment report', MARGIN, FOOTER_Y + 6.2, 145, 6.8, MUTED);
  doc.setFont('helvetica', 'bold');
  doc.setFontSize(7.2);
  setText(doc);
  doc.text(`${page} / ${total}`, PAGE_WIDTH - MARGIN, FOOTER_Y + 6.2, { align: 'right' });
}

function topRule(doc: jsPDF) {
  doc.setFillColor(...PURPLE);
  doc.rect(0, 0, PAGE_WIDTH, 3, 'F');
}

function renderLogo(doc: jsPDF, assets: SuitabilityRplReportAssets) {
  if (assets.logoDataUrl) {
    try {
      doc.addImage(assets.logoDataUrl, 'PNG', MARGIN, 9, 38, 18, undefined, 'FAST');
      return;
    } catch {
      // The report remains usable if the public brand asset is unavailable.
    }
  }
  doc.setFillColor(...PURPLE_WASH);
  doc.setDrawColor(...PURPLE_LINE);
  doc.roundedRect(MARGIN, 10, 12, 12, 2.4, 2.4, 'FD');
  setLine(doc, PURPLE, 1.1);
  doc.line(MARGIN + 3.5, 18.7, MARGIN + 6, 14.5);
  doc.line(MARGIN + 6, 14.5, MARGIN + 8.7, 18.7);
  label(doc, 'KENT BUSINESS COLLEGE', MARGIN + 16, 17.5, 7.7);
}

function coverHeader(doc: jsPDF, assets: SuitabilityRplReportAssets, status: SuitabilityRplReportData['status']) {
  topRule(doc);
  renderLogo(doc, assets);
  label(doc, 'ENROLMENT SYSTEM  /  LEARNER ASSESSMENT', 78, 17.1, 6.4);
  pill(doc, status === 'finalised' ? 'FINALISED' : status === 'draft' ? 'DRAFT REPORT' : 'BLANK TEMPLATE', 163, 11.8, 32);
  doc.setFont('helvetica', 'bold');
  doc.setFontSize(16.8);
  setText(doc);
  doc.text(['Suitability & Recognition', 'of Prior Learning Report'], MARGIN, 36.5, { lineHeightFactor: 1.12 });
  blankLine(doc, MARGIN, 51.5, CONTENT_WIDTH);
}

function runningHeader(doc: jsPDF, subtitle: string, status?: SuitabilityRplReportData['status']) {
  topRule(doc);
  label(doc, 'SUITABILITY & RECOGNITION OF PRIOR LEARNING REPORT', MARGIN, 16.8, 8.6);
  pill(doc, status === 'finalised' ? 'FINALISED' : status === 'draft' ? 'DRAFT' : 'TEMPLATE', 166, 10.5, 29);
  doc.setFont('helvetica', 'normal');
  doc.setFontSize(8.2);
  doc.setCharSpace(0.55);
  setText(doc, MUTED);
  doc.text(subtitle, MARGIN, 26);
  doc.setCharSpace(0);
  blankLine(doc, MARGIN, 31.5, CONTENT_WIDTH);
}

function detailField(doc: jsPDF, x: number, y: number, width: number, heading: string, fieldValue = '') {
  box(doc, x, y, width, 16.5);
  label(doc, heading, x + 4, y + 5.6, 6.7);
  if (fieldValue) body(doc, fieldValue, x + 4, y + 11, width - 8, 7.6);
  else blankLine(doc, x + 4, y + 12.7, width - 8);
}

function responseBox(doc: jsPDF, y: number, questionNumber: number, question: string, response = '', height = 32) {
  box(doc, MARGIN, y, CONTENT_WIDTH, height);
  pill(doc, `QUESTION ${questionNumber}`, MARGIN + 4, y + 4, 30);
  body(doc, question, MARGIN + 4, y + 17.2, CONTENT_WIDTH - 8, 8.3);
  if (response) body(doc, response, MARGIN + 4, y + 24, CONTENT_WIDTH - 8, 7.6, MUTED);
  else blankLine(doc, MARGIN + 4, y + height - 6.5, CONTENT_WIDTH - 8);
}

type RadarSummaryRow = { label: string; score?: string | number | null; answered?: number; total?: number };

function normalizedRadarSummary(rows: RadarSummaryRow[] = []): RadarSummaryRow[] {
  const saved = new Map(rows.map((row) => [row.label, row]));
  return ['Knowledge', 'Skills', 'Behaviours'].map((label) => saved.get(label) || ({ label, score: null, answered: 0, total: 0 }));
}

function radar(doc: jsPDF, cx: number, cy: number, radius: number, groups: RadarSummaryRow[] = []) {
  const axes = 3;
  setLine(doc, PURPLE_LINE, 0.32);
  for (let ring = 1; ring <= 5; ring += 1) {
    const r = radius * (ring / 5);
    const points = Array.from({ length: axes }, (_, index) => {
      const angle = (-Math.PI / 2) + ((Math.PI * 2 * index) / axes);
      return [cx + Math.cos(angle) * r, cy + Math.sin(angle) * r] as const;
    });
    points.forEach((point, index) => {
      const next = points[(index + 1) % points.length];
      doc.line(point[0], point[1], next[0], next[1]);
    });
  }
  for (let index = 0; index < axes; index += 1) {
    const angle = (-Math.PI / 2) + ((Math.PI * 2 * index) / axes);
    doc.line(cx, cy, cx + Math.cos(angle) * radius, cy + Math.sin(angle) * radius);
  }
  [1, 5, 9].forEach((marker) => {
    const markerRadius = radius * (marker / 9);
    body(doc, String(marker), cx - radius - 7, cy - markerRadius + 1.5, 5, 5.4, MUTED);
  });
  label(doc, groups[0]?.label || 'Knowledge', cx - 14, cy - radius - 5, 6.1, PURPLE_DARK);
  label(doc, groups[1]?.label || 'Skills', cx + radius - 1, cy + radius + 5, 6.1, PURPLE_DARK);
  rightLabel(doc, groups[2]?.label || 'Behaviours', cx - radius + 15, cy + radius + 5, 6.1, PURPLE_DARK);
  const values = groups.slice(0, axes).map((group) => {
    const numeric = Number(group.score);
    return Number.isFinite(numeric) && numeric >= 1 && numeric <= 9 ? numeric : null;
  });
  const points = Array.from({ length: axes }, (_, index) => {
      const numeric = values[index];
      const angle = (-Math.PI / 2) + ((Math.PI * 2 * index) / axes);
      const r = radius * ((numeric || 0) / 9);
      return [cx + Math.cos(angle) * r, cy + Math.sin(angle) * r] as const;
    });
  if (values.every((value) => value != null)) {
    doc.setFillColor(188, 157, 216);
    setLine(doc, PURPLE, 0.8);
    points.forEach((point, index) => {
      const next = points[(index + 1) % points.length];
      doc.line(point[0], point[1], next[0], next[1]);
      doc.circle(point[0], point[1], 1, 'F');
    });
  } else {
    doc.setFillColor(188, 157, 216);
    values.forEach((value, index) => {
      if (value == null) return;
      doc.circle(points[index][0], points[index][1], 1, 'F');
    });
  }
}

function evidenceCard(doc: jsPDF, y: number, height: number, title: string, code: string, choices: string[], prompt: string, detail = '') {
  box(doc, MARGIN, y, CONTENT_WIDTH, height);
  doc.setFillColor(...PURPLE);
  doc.roundedRect(MARGIN, y, 1.5, height, 0.7, 0.7, 'F');
  label(doc, title, MARGIN + 6, y + 7, 8.6);
  rightLabel(doc, code, PAGE_WIDTH - MARGIN - 5, y + 7, 6.7);
  const normalized = detail.toLowerCase();
  choices.forEach((choice, index) => checkbox(
    doc,
    MARGIN + 6 + (index * 39),
    y + 10.5,
    choice,
    Boolean(detail) && normalized.startsWith(choice.toLowerCase()),
  ));
  body(doc, prompt, MARGIN + 6, y + 22, CONTENT_WIDTH - 12, 7.1);
  if (detail) body(doc, detail, MARGIN + 6, y + 28, CONTENT_WIDTH - 12, 7.2, MUTED);
  else blankLine(doc, MARGIN + 6, y + height - 5.5, CONTENT_WIDTH - 12);
}

function signatureCard(doc: jsPDF, x: number, y: number, width: number, title: string, declaration: string) {
  box(doc, x, y, width, 65);
  doc.setFillColor(...PURPLE_WASH);
  doc.roundedRect(x, y, width, 11, 2.5, 2.5, 'F');
  doc.rect(x, y + 7, width, 4, 'F');
  label(doc, title, x + 4.5, y + 7.5, width < 70 ? 6.2 : 8.3);
  body(doc, declaration, x + 4.5, y + 18.5, width - 9, 7.4);
  label(doc, 'NAME', x + 4.5, y + 39.5, 6.5);
  blankLine(doc, x + 4.5, y + 45, width - 9);
  label(doc, 'SIGNATURE', x + 4.5, y + 51.5, 6.5);
  blankLine(doc, x + 4.5, y + 56.8, width - 9);
  label(doc, 'DATE', x + 4.5, y + 62.1, 6.5);
  blankLine(doc, x + 22, y + 62.2, width - 26.5);
}

function firstPage(doc: jsPDF, data: SuitabilityRplReportData, assets: SuitabilityRplReportAssets) {
  coverHeader(doc, assets, data.status);
  sectionHeading(doc, 1, SUITABILITY_RPL_SECTION_TITLES[0], 'AUTO-POPULATED', 62);
  const learner = data.learner ?? {};
  const fields = [
    ['LEARNER FULL NAME', value(learner.fullName)],
    ['LEARNER REFERENCE / ID', value(learner.reference)],
    ['EMPLOYER / ORGANISATION', value(learner.employer)],
    ['JOB TITLE', value(learner.jobTitle)],
    ['APPRENTICESHIP PROGRAMME / STANDARD', value(learner.programme)],
    ['PROGRAMME LEVEL', value(learner.level)],
    ['ASSESSOR / ADMINISTRATOR', value(learner.assessor)],
    ['ASSESSMENT DATE', value(formatSuitabilityRplDate(learner.assessmentDate))],
  ];
  fields.forEach(([heading, fieldValue], index) => {
    const column = index % 2;
    const row = Math.floor(index / 2);
    detailField(doc, MARGIN + (column * 91.5), 68 + (row * 18.5), 88.5, heading, fieldValue);
  });

  sectionHeading(doc, 2, SUITABILITY_RPL_SECTION_TITLES[1], 'LEARNER RESPONSES', 149);
  const first = understandingAt(data, 0);
  const second = understandingAt(data, 1);
  responseBox(doc, 155, 1, first.question, excerpt(first.answer, 260), 29);
  responseBox(doc, 187, 2, second.question, excerpt(second.answer, 300), 32);

  sectionHeading(doc, 3, SUITABILITY_RPL_SECTION_TITLES[2], 'ASSESSMENT EVIDENCE', 228);
  box(doc, MARGIN, 234, CONTENT_WIDTH, 35, PURPLE_WASH);
  const suitability = suitabilityOf(data);
  const contextFields = [
    ['JOB TITLE', value(data.learner?.jobTitle) || 'Not available', 20, 242, 48],
    ['PROGRAMME NAME', value(data.learner?.programme) || 'Not available', 78, 242, 58],
    ['PROGRAMME SUITABILITY PATHWAY', pathwayLabel(suitability.pathway), 146, 242, 54],
  ] as const;
  contextFields.forEach(([heading, fieldValue, x, headingY, width]) => {
    label(doc, heading, x, headingY, 6.2, PURPLE_DARK);
    body(doc, fieldValue, x, headingY + 8, width, 7.2);
  });
  const hasSavedSuitability = Boolean(suitability.responses?.length || suitability.justification);
  body(doc, hasSavedSuitability
    ? 'Complete saved learner responses and the separately approved suitability justification continue on the following page(s).'
    : 'Saved learner responses and an approved suitability justification will appear here when available.',
  MARGIN + 5, 262, CONTENT_WIDTH - 10, 7, MUTED);
}

function programmeSuitabilityPages(doc: jsPDF, data: SuitabilityRplReportData) {
  const suitability = suitabilityOf(data);
  const responses = suitability.responses || [];
  const groupedResponses = new Map<string, string[]>();
  responses.forEach((response) => {
    const heading = value(response.group);
    const options = (response.selectedOptions?.map((option) => option.label) || response.answers || [])
      .map(value)
      .filter((answer) => answer.trim());
    if (!options.length) return;
    const existing = groupedResponses.get(heading) || [];
    groupedResponses.set(heading, [...existing, ...options]);
  });
  const groups = Array.from(groupedResponses.entries()).map(([heading, options]) => ({ heading, options }));
  const finalJustification = suitability.reviewed ? value(suitability.justification).trim() : '';
  if (!groups.length && !suitability.justification) return;

  doc.addPage('a4', 'portrait');
  runningHeader(doc, 'PROGRAMME SUITABILITY RESPONSES', data.status);
  sectionHeading(doc, 3, SUITABILITY_RPL_SECTION_TITLES[2], 'LEARNER SUBMISSION', 43);
  let contentY = 57;

  if (groups.length) {
    autoTable(doc, {
      startY: contentY,
      margin: { left: MARGIN, right: MARGIN, top: 36, bottom: 22 },
      head: [['GROUP', 'ANSWERS']],
      body: groups.map((group) => [group.heading || 'Programme suitability', group.options.join('\n')]),
      theme: 'grid',
      showHead: 'everyPage',
      rowPageBreak: 'avoid',
      styles: {
        font: 'helvetica', fontSize: 8, cellPadding: 3.2,
        lineColor: [...PURPLE_LINE], lineWidth: 0.3, textColor: [...INK], overflow: 'linebreak',
        valign: 'top', minCellHeight: 18,
      },
      headStyles: {
        fillColor: [...PURPLE_WASH], textColor: [...PURPLE_DARK],
        fontStyle: 'bold', fontSize: 7.2, lineColor: [...PURPLE_LINE], lineWidth: 0.3,
      },
      columnStyles: {
        0: { cellWidth: CONTENT_WIDTH * 0.34, fontStyle: 'bold', textColor: [...PURPLE_DARK] },
        1: { cellWidth: CONTENT_WIDTH * 0.66 },
      },
      didDrawPage: ({ pageNumber }) => {
        if (pageNumber > 1) runningHeader(doc, 'PROGRAMME SUITABILITY RESPONSES - CONTINUATION', data.status);
      },
    });
    contentY = (doc as jsPDF & { lastAutoTable: { finalY: number } }).lastAutoTable.finalY + 8;
  }

  // Keep the justification heading and its content box together instead of
  // leaving a heading orphaned at the bottom of the preceding page.
  if (contentY > 220) {
    doc.addPage('a4', 'portrait');
    runningHeader(doc, 'PROGRAMME SUITABILITY JUSTIFICATION', data.status);
    contentY = 43;
  }
  box(doc, MARGIN, contentY, CONTENT_WIDTH, 14, PURPLE_WASH);
  label(doc, 'PROGRAMME SUITABILITY JUSTIFICATION', MARGIN + 5, contentY + 8, 7.4, PURPLE_DARK);
  autoTable(doc, {
    startY: contentY + 16,
    margin: { left: MARGIN, right: MARGIN, top: 36, bottom: 22 },
    body: [[finalJustification]],
    theme: 'grid',
    rowPageBreak: 'avoid',
    styles: {
      font: 'helvetica', fontSize: 7.8, cellPadding: 3.2,
      lineColor: [...PURPLE_LINE], lineWidth: 0.3, textColor: [...INK], overflow: 'linebreak',
      minCellHeight: 20,
    },
    columnStyles: { 0: { cellWidth: CONTENT_WIDTH } },
    willDrawPage: ({ pageNumber }) => {
      if (pageNumber > 1) runningHeader(doc, 'PROGRAMME SUITABILITY JUSTIFICATION - CONTINUATION', data.status);
    },
  });
}

function secondPage(doc: jsPDF, data: SuitabilityRplReportData) {
  runningHeader(doc, 'SKILLS RADAR AND SUPPORTING EVIDENCE', data.status);
  sectionHeading(doc, 4, SUITABILITY_RPL_SECTION_TITLES[3], 'ASSESSMENT DATA', 43);

  box(doc, MARGIN, 51, 87, 74);
  label(doc, 'SKILLS RADAR VISUAL', MARGIN + 5, 58, 7);
  const summary = normalizedRadarSummary(data.skillsRadarSummary || []);
  radar(doc, 58.5, 88, 22, summary);
  body(doc, 'Grouped averages from saved Skills Radar answers.', MARGIN + 6, 119, 73, 6.7, MUTED);

  box(doc, 105, 51, 90, 74, PURPLE_WASH);
  doc.setFillColor(...PURPLE_WASH);
  doc.rect(105, 51, 90, 12, 'F');
  label(doc, 'CATEGORY', 109, 59.5, 6.8);
  rightLabel(doc, 'AVERAGE / 9', 190, 59.5, 6.8);
  summary.forEach((row, index) => {
    const y = 69 + (index * 17);
    body(doc, row.label, 109, y + 2.5, 48, 7);
    rightLabel(doc, row.score == null ? 'N/A' : `${value(row.score)} / 9`, 190, y + 2.5, 7, row.score == null ? MUTED : INK);
    body(doc, `${row.answered ?? 0}/${row.total ?? 0} answered`, 109, y + 8.5, 70, 6.2, MUTED);
    if (index < summary.length - 1) blankLine(doc, 109, y + 12.5, 81);
  });

  let evidenceHeadingY = 137;
  if ((data.skillsRadar?.length || 0) > 0) {
    label(doc, 'COMPLETE SAVED SCORE TABLE', MARGIN, 135, 6.8, PURPLE_DARK);
    autoTable(doc, {
      startY: 139,
      margin: { left: MARGIN, right: MARGIN, top: 36, bottom: 20 },
      head: [['KSB / COMPETENCY', 'SAVED SCORE']],
      body: (data.skillsRadar || []).map((row) => [row.label, value(row.score)]),
      theme: 'grid',
      styles: { font: 'helvetica', fontSize: 7, cellPadding: 2, lineColor: [...PURPLE_LINE], lineWidth: 0.25, textColor: [...INK] },
      headStyles: { fillColor: [...PURPLE_WASH], textColor: [...PURPLE_DARK], fontStyle: 'bold' },
      columnStyles: { 1: { cellWidth: 28, halign: 'center' } },
      willDrawPage: ({ pageNumber }) => {
        if (pageNumber > 1) runningHeader(doc, 'SKILLS RADAR — SAVED SCORE CONTINUATION', data.status);
      },
    });
    const finalY = (doc as jsPDF & { lastAutoTable: { finalY: number } }).lastAutoTable.finalY;
    doc.addPage('a4', 'portrait');
    runningHeader(doc, 'CV, QUALIFICATIONS AND PLR EVIDENCE', data.status);
    evidenceHeadingY = 43;
  }
  sectionHeading(doc, 5, SUITABILITY_RPL_SECTION_TITLES[4], 'EVIDENCE REVIEW', evidenceHeadingY);
  const firstCardY = evidenceHeadingY + 7;
  evidenceCard(doc, firstCardY, 35, 'Curriculum Vitae (CV)', 'A / CV', ['Provided', 'Not provided', 'Not available'], 'Relevant job experience and responsibilities:', data.evidence?.cv);
  evidenceCard(doc, firstCardY + 38, 35, 'Qualifications', 'B / QUALIFICATIONS', ['Provided', 'Not provided', 'Not available'], 'Relevant qualification, level and prior coverage:', data.evidence?.qualifications);
  evidenceCard(doc, firstCardY + 76, 45, 'Personal Learning Record (PLR)', 'C / PLR', ['Available', 'Not available'], 'Relevant prior achievement and learning evidence:', data.evidence?.plr);
}

function thirdPage(doc: jsPDF, data: SuitabilityRplReportData) {
  runningHeader(doc, 'RECOGNITION OF PRIOR LEARNING - ADMIN DECISION', data.status);
  sectionHeading(doc, 6, SUITABILITY_RPL_SECTION_TITLES[5], 'ADMIN DECISION', 43);

  box(doc, MARGIN, 51, CONTENT_WIDTH, 24, PURPLE_WASH);
  body(doc, 'The assessor records whether verified prior learning overlaps with the programme requirements and whether a reduction in planned training is justified.', MARGIN + 5, 60, CONTENT_WIDTH - 10, 8.1, INK, 1.42);

  box(doc, MARGIN, 79, CONTENT_WIDTH, 25);
  label(doc, 'RPL DECISION', MARGIN + 5, 86, 6.8);
  checkbox(doc, MARGIN + 5, 91, 'No relevant prior learning', data.rpl?.relevantPriorLearning === false, 45);
  checkbox(doc, MARGIN + 69, 91, 'Relevant, no reduction', data.rpl?.relevantPriorLearning === true && data.rpl?.reductionJustified === false, 42);
  checkbox(doc, MARGIN + 130, 91, 'Reduction justified', data.rpl?.reductionJustified === true, 37);

  box(doc, MARGIN, 109, CONTENT_WIDTH, 33);
  label(doc, 'ASSESSOR EVIDENCE REVIEW', MARGIN + 5, 116, 7);
  body(doc, 'The decision must be based on verified evidence; PLR being unavailable is not evidence that prior learning is absent.', MARGIN + 5, 124, CONTENT_WIDTH - 10, 7.1, MUTED);
  if (data.rpl?.evidenceReview) body(doc, excerpt(data.rpl.evidenceReview, 310), MARGIN + 5, 134, CONTENT_WIDTH - 10, 7.2);
  else {
    blankLine(doc, MARGIN + 5, 135, CONTENT_WIDTH - 10);
  }

  box(doc, MARGIN, 147, CONTENT_WIDTH, 40);
  pill(doc, 'NO REDUCTION', MARGIN + 4.5, 151.5, 32);
  label(doc, 'Reason for retaining the full programme', MARGIN + 40, 157, 8.3);
  body(doc, 'Required when no training reduction is justified.', MARGIN + 5, 166, CONTENT_WIDTH - 10, 7.2, MUTED);
  if (data.rpl?.noReductionReason) body(doc, excerpt(data.rpl.noReductionReason, 360), MARGIN + 5, 175, CONTENT_WIDTH - 10, 7.2);
  else {
    blankLine(doc, MARGIN + 5, 177, CONTENT_WIDTH - 10);
    blankLine(doc, MARGIN + 5, 182, CONTENT_WIDTH - 10);
  }

  const rplRows = data.rpl?.rows ?? [];
  const showReductionTable = data.rpl?.reductionJustified === true || data.rpl?.reductionJustified == null;
  let contentY = 193;
  if (showReductionTable) {
    label(doc, 'MODULE-LEVEL PRIOR LEARNING AND REDUCTION', MARGIN, contentY, 7, PURPLE_DARK);
    autoTable(doc, {
      startY: contentY + 4,
      margin: { left: MARGIN, right: MARGIN, top: 36, bottom: 22 },
      head: [['MODULE / AREA', 'KSB LINK', 'VERIFIED EVIDENCE', 'HOURS']],
      body: rplRows.length ? rplRows.map((row) => [value(row.area), value(row.ksb), value(row.evidence), value(row.hours)]) : [['', '', '', ''], ['', '', '', ''], ['', '', '', '']],
      theme: 'grid',
      styles: { font: 'helvetica', fontSize: 6.5, cellPadding: 1.8, minCellHeight: 9, lineColor: [...PURPLE_LINE], lineWidth: 0.25, textColor: [...INK], overflow: 'linebreak' },
      headStyles: { fillColor: [...PURPLE_WASH], textColor: [...PURPLE_DARK], fontStyle: 'bold' },
      columnStyles: { 0: { cellWidth: 52 }, 1: { cellWidth: 28 }, 2: { cellWidth: 76 }, 3: { cellWidth: 24, halign: 'center' } },
      willDrawPage: ({ pageNumber }) => {
        if (pageNumber > 1) runningHeader(doc, 'RPL MODULE REDUCTIONS — CONTINUATION', data.status);
      },
    });
    contentY = (doc as jsPDF & { lastAutoTable: { finalY: number } }).lastAutoTable.finalY + 6;
  }

  const hourCards = [
    ['ORIGINAL HOURS', data.rpl?.originalHours],
    ['REDUCTION', data.rpl?.reductionHours],
    ['ADJUSTED HOURS', data.rpl?.adjustedHours],
  ] as const;
  if (contentY > 251) {
    doc.addPage('a4', 'portrait');
    runningHeader(doc, 'RPL ASSESSMENT — HOURS AND JUSTIFICATION', data.status);
    contentY = 41;
  }
  const cardWidth = 53;
  hourCards.forEach(([heading, hours], index) => {
    const x = MARGIN + 5 + (index * (cardWidth + 5.5));
    box(doc, x, contentY, cardWidth, 19);
    label(doc, heading, x + 3.5, contentY + 6, 6.2);
    if (hours != null && value(hours)) body(doc, `${value(hours)} hours`, x + 3.5, contentY + 14, cardWidth - 7, 7.2);
    else blankLine(doc, x + 3.5, contentY + 15, cardWidth - 7);
  });
  contentY += 25;
  body(doc, 'Recorded reductions do not automatically modify the authoritative Training Plan or funding calculations.', MARGIN + 5, contentY, CONTENT_WIDTH - 10, 6.9, MUTED);
  contentY += 8;
  const finalRplJustification = data.rpl?.reviewed ? value(data.rpl.justification).trim() : '';
  if (finalRplJustification) {
    if (contentY > 235) {
      doc.addPage('a4', 'portrait');
      runningHeader(doc, 'RPL JUSTIFICATION — CONTINUATION', data.status);
      contentY = 41;
    }
    autoTable(doc, {
      startY: contentY,
      margin: { left: MARGIN, right: MARGIN, top: 36, bottom: 22 },
      body: [[finalRplJustification]],
      theme: 'grid',
      styles: { font: 'helvetica', fontSize: 7.2, cellPadding: 3, lineColor: [...PURPLE_LINE], lineWidth: 0.3, textColor: [...INK], overflow: 'linebreak' },
      headStyles: { fillColor: [...PURPLE_WASH], textColor: [...PURPLE_DARK], fontStyle: 'bold' },
      willDrawPage: ({ pageNumber }) => {
        if (pageNumber > 1) runningHeader(doc, 'RPL JUSTIFICATION — CONTINUATION', data.status);
      },
    });
  }
}

function fourthPage(doc: jsPDF, data: SuitabilityRplReportData) {
  runningHeader(doc, 'ASSESSMENT CONCLUSION AND SIGN-OFF', data.status);
  sectionHeading(doc, 7, SUITABILITY_RPL_SECTION_TITLES[6], 'FINAL ASSESSMENT', 43);

  box(doc, MARGIN, 51, CONTENT_WIDTH, 21);
  label(doc, 'SUITABILITY OUTCOME', MARGIN + 5, 58, 6.7);
  checkbox(doc, MARGIN + 5, 62, 'Suitable', data.finalAssessment?.outcome === 'suitable');
  checkbox(doc, MARGIN + 60, 62, 'Further assessment required', data.finalAssessment?.outcome === 'further_assessment', 75);

  box(doc, MARGIN, 77, CONTENT_WIDTH, 40);
  label(doc, 'ASSESSMENT RATIONALE', MARGIN + 5, 85, 7.2);
  body(doc, 'Evidence-based explanation for programme suitability and the recorded prior learning outcome.', MARGIN + 5, 94, CONTENT_WIDTH - 10, 7.3, MUTED);
  if (data.finalAssessment?.rationale) body(doc, excerpt(data.finalAssessment.rationale, 360), MARGIN + 5, 104, CONTENT_WIDTH - 10, 7.2);
  else {
    blankLine(doc, MARGIN + 5, 105, CONTENT_WIDTH - 10);
    blankLine(doc, MARGIN + 5, 112, CONTENT_WIDTH - 10);
  }

  box(doc, MARGIN, 122, CONTENT_WIDTH, 32, PURPLE_WASH);
  label(doc, 'FINAL TRAINING RECOMMENDATION', MARGIN + 5, 130, 7.2);
  checkbox(doc, MARGIN + 5, 135, 'Full programme', data.rpl?.reductionJustified === false);
  checkbox(doc, MARGIN + 73, 135, 'Adjusted training plan', data.rpl?.reductionJustified === true);
  body(doc, 'The recorded training plan and programme hours must match the assessor-approved decision.', MARGIN + 5, 148, CONTENT_WIDTH - 10, 7, MUTED);

  box(doc, MARGIN, 159, CONTENT_WIDTH, 26);
  label(doc, 'DOCUMENT CONTROL', MARGIN + 5, 167, 7.2);
  const controls = [
    ['REPORT REFERENCE', data.finalAssessment?.reportReference],
    ['VERSION', data.finalAssessment?.version],
    ['APPROVAL DATE', data.finalAssessment?.approvalDate],
  ] as const;
  controls.forEach(([heading, controlValue], index) => {
    const x = MARGIN + 5 + (index * 60);
    label(doc, heading, x, 174, 6.1);
    if (controlValue) body(doc, controlValue, x, 180.5, 50, 6.8);
    else blankLine(doc, x, 181, 52);
  });

  sectionHeading(doc, 8, SUITABILITY_RPL_SECTION_TITLES[7], 'ASSESSOR + LEARNER + EMPLOYER', 197);
  signatureCard(doc, MARGIN, 204, 57.5, 'ASSESSOR / ADMIN', 'I have reviewed the evidence and approve the recorded assessment and training recommendation.');
  signatureCard(doc, 76.25, 204, 57.5, 'LEARNER', 'The assessment and its outcome have been discussed with me, and I acknowledge this record.');
  signatureCard(doc, 137.5, 204, 57.5, 'EMPLOYER / LINE MANAGER', 'I have discussed the assessment and support the recorded programme recommendation.');
}

function addNarrativeContinuations(doc: jsPDF, data: SuitabilityRplReportData) {
  const blocks = [
    ...[0, 1].map((index) => {
      const item = understandingAt(data, index);
      return item.answer.length > (index === 0 ? 260 : 300) ? [item.question, item.answer] : null;
    }),
    data.rpl?.evidenceReview && data.rpl.evidenceReview.length > 310
      ? ['Assessor evidence review', data.rpl.evidenceReview]
      : null,
    data.rpl?.noReductionReason && data.rpl.noReductionReason.length > 360
      ? ['Reason for retaining the full programme', data.rpl.noReductionReason]
      : null,
    data.finalAssessment?.rationale && data.finalAssessment.rationale.length > 360
      ? ['Final assessor summary', data.finalAssessment.rationale]
      : null,
  ].filter((item): item is string[] => Boolean(item));
  if (!blocks.length) return;
  doc.addPage('a4', 'portrait');
  runningHeader(doc, 'ASSESSMENT NARRATIVE — CONTINUATION', data.status);
  autoTable(doc, {
    startY: 40,
    margin: { left: MARGIN, right: MARGIN, top: 36, bottom: 22 },
    head: [['FIELD', 'COMPLETE SAVED RESPONSE']],
    body: blocks,
    theme: 'grid',
    styles: { font: 'helvetica', fontSize: 7.2, cellPadding: 3, lineColor: [...PURPLE_LINE], lineWidth: 0.3, textColor: [...INK], overflow: 'linebreak' },
    headStyles: { fillColor: [...PURPLE_WASH], textColor: [...PURPLE_DARK], fontStyle: 'bold' },
    columnStyles: { 0: { cellWidth: 55, fontStyle: 'bold' } },
    willDrawPage: ({ pageNumber }) => {
      if (pageNumber > 1) runningHeader(doc, 'ASSESSMENT NARRATIVE — CONTINUATION', data.status);
    },
  });
}

function addDocumentFooters(doc: jsPDF, blank: boolean) {
  const total = doc.getNumberOfPages();
  for (let page = 1; page <= total; page += 1) {
    doc.setPage(page);
    pageFooter(doc, page, total, blank);
  }
}

export function buildSuitabilityRplReportPdf(
  data: SuitabilityRplReportData = {},
  assets: SuitabilityRplReportAssets = {},
): jsPDF {
  const doc = new jsPDF({ orientation: 'portrait', unit: 'mm', format: 'a4', compress: true, putOnlyUsedFonts: true });
  doc.setProperties({
    title: 'Suitability & Recognition of Prior Learning Report',
    subject: data.status ? 'Suitability and recognition of prior learning assessment' : 'Blank enrolment assessment template',
    author: 'Kent Business College',
    creator: 'Enrolment System',
  });
  firstPage(doc, data, assets);
  addNarrativeContinuations(doc, data);
  programmeSuitabilityPages(doc, data);
  doc.addPage('a4', 'portrait');
  secondPage(doc, data);
  doc.addPage('a4', 'portrait');
  thirdPage(doc, data);
  doc.addPage('a4', 'portrait');
  fourthPage(doc, data);
  addDocumentFooters(doc, !data.status);
  return doc;
}

function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error ?? new Error('Could not read the organisation logo.'));
    reader.onload = () => resolve(String(reader.result ?? ''));
    reader.readAsDataURL(blob);
  });
}

export async function loadSuitabilityRplReportAssets(signal?: AbortSignal): Promise<SuitabilityRplReportAssets> {
  try {
    const response = await fetch(`${import.meta.env.BASE_URL}assets/kbc-logo.png`, { signal });
    if (!response.ok) return {};
    return { logoDataUrl: await blobToDataUrl(await response.blob()) };
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === 'AbortError') throw cause;
    return {};
  }
}

export async function createBlankSuitabilityRplReportPdf(signal?: AbortSignal): Promise<jsPDF> {
  return buildSuitabilityRplReportPdf({}, await loadSuitabilityRplReportAssets(signal));
}

export const suitabilityRplReportFilename = 'Suitability-and-RPL-Blank-Template.pdf';

export function suitabilityRplPopulatedReportFilename(learnerName = '', reportReference = '') {
  const stem = [learnerName, reportReference].filter(Boolean).join('-') || 'Draft';
  return `Suitability-and-RPL-${stem.replace(/[^a-z0-9_-]+/gi, '-').replace(/-+/g, '-')}.pdf`;
}
