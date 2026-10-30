// ============================================================================
// Training Plan — printable tripartite document
//
// Follows the Training Plan template: programme details, employment details,
// key milestones, the commitments each of the three parties makes, the learning
// plan table, off-the-job training hours, End Point Assessment, the three-way
// signature block, and the contact appendices.
//
// Signed by all THREE parties — apprentice, employer and training provider —
// which is what distinguishes it from the Apprenticeship Agreement (apprentice
// + employer) and the ILR (learner + provider).
//
// Values come from the issued document's frozen snapshot, so a signed plan
// prints what was signed.
// ============================================================================
import jsPDF from 'jspdf';

const PAGE_W = 210;
const PAGE_H = 297;
const MARGIN_X = 18;
const MARGIN_TOP = 20;
const MARGIN_BOTTOM = 22;
const CONTENT_W = PAGE_W - MARGIN_X * 2;
const LABEL_W = 74;

export interface TrainingPlanProgramme {
  apprenticeName?: string;
  programme?: string;
  standard?: string;
  reference?: string;
  level?: string;
  startDate?: string;
  endDate?: string;
  practicalStartDate?: string;
  practicalEndDate?: string;
  durationWeeks?: number | null;
  ilrPlannedHours?: number | null;
}

export interface TrainingPlanEmployment {
  employerName?: string;
  deliveryAddress?: string;
  jobTitle?: string;
  workingHoursPerWeek?: string;
  lineManager?: string;
  lineManagerTitle?: string;
  startDateWithEmployer?: string;
}

export interface TrainingPlanComponent {
  componentId?: string;
  title?: string;
  /** Readable delivery method, from the component's type. */
  method?: string;
  otjHours?: number | null;
}

export interface TrainingPlanWeek {
  weekNumber?: number | null;
  weekTitle?: string;
  components?: TrainingPlanComponent[];
}

export interface TrainingPlanRow {
  activity?: string;
  method?: string;
  deliveryLead?: string;
  plannedDate?: string;
  plannedEmHours?: number | null;
  plannedOtjHours?: number | null;
  /** The weeks and components that deliver this module. */
  weeks?: TrainingPlanWeek[];
  componentHours?: number | null;
  componentCount?: number | null;
}

export interface TrainingPlanSignature {
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

/** The commitments each party makes, abridged from the template's wording. */
const COMMITMENTS: { heading: string; intro: string; items: string[] }[] = [
  {
    heading: 'The main provider will:',
    intro: 'In signing this Training Plan, the provider agrees and commits to:',
    items: [
      'Check the eligibility of the learner, including their right to work in England, that they spend at least 50% of their working time in England, that they are not undertaking another apprenticeship or benefiting from other DfE funding, and that they have not been asked to contribute financially.',
      'Conduct the required checks with the employer, including that an apprenticeship agreement has been signed, a lawful wage is paid, the apprenticeship is the most appropriate programme, and that the employer acknowledges the apprentice needs at least an average of 6 hours per week of off-the-job training during working hours.',
      'Devise and deliver the agreed off-the-job training and arrange the assessments required, taking account of the learner’s prior knowledge, skills and behaviours, to achieve the milestones in this Training Plan.',
      'Negotiate a price with the employer, accounting for additional payments, relevant prior learning and any co-investment obligations.',
      'Ensure the provision of appropriate additional learning support where required.',
      'Ensure a safe and fair learning and working environment.',
      'Brief the apprentice on the provider’s policies: Code of Conduct, Health & Safety, Equality and Diversity, Harassment and Bullying, and the Complaints Procedure.',
      'Undertake legal and contractual obligations in relation to Safeguarding and Prevent.',
    ],
  },
  {
    heading: 'The employer will:',
    intro: 'In signing this Training Plan, the employer agrees and commits to:',
    items: [
      'Work with the main provider to identify the most suitable apprenticeship standard.',
      'Directly employ (PAYE) the apprentice and issue a contract of employment.',
      'Employ the apprentice for at least the duration required to complete the apprenticeship.',
      'Arrange contracted hours to allow for sufficient, regular training.',
      'Pay the apprentice at least the National Minimum Wage and issue payslips.',
      'Provide an appropriate workplace induction.',
      'Provide the on-the-job training, mentoring and support required to achieve the milestones in this Training Plan.',
      'Promptly inform the provider if any matters arise that may affect the learner’s learning, development or progression.',
      'Ensure conformity with the provider’s Equality and Diversity policies and procedures.',
      'Undertake legal and contractual obligations in relation to Safeguarding and Prevent.',
      'Allow the apprentice to complete the apprenticeship within their working hours, including any English and maths required.',
    ],
  },
  {
    heading: 'The apprentice will:',
    intro: 'In signing this Training Plan, the learner agrees and commits to:',
    items: [
      'Give the provider relevant information to assist in learner or programme eligibility checks.',
      'Take appropriate responsibility for their own learning, development and progression, including attending all required training sessions.',
      'Diligently undertake the on- and off-the-job learning required to achieve the milestones in this document.',
      'Maintain an off-the-job training log evidencing the hours set out in this training plan, presenting it at every appointment with their Tutor.',
      'Maintain appointments with their Tutor and Functional Skills Tutors.',
      'Prepare for, participate in and contribute to reviews of their progress and achievement.',
      'Work collaboratively with the employer and the provider, including submitting coursework for formative assessment where required.',
      'Promptly inform the employer and/or the provider if any matters arise that may affect their learning, development or progression.',
      'Behave at all times in a safe and responsible manner, in accordance with Health and Safety legislation.',
      'Report any incidents of harassment, bullying, violence or suspected extremism or radicalisation.',
      'Comply with the policies and procedures of the employer.',
      'Provide the End-Point Assessment Organisation with the information necessary to register for End-Point Assessment.',
    ],
  },
];

export function buildTrainingPlanPdf(
  programme: TrainingPlanProgramme,
  employment: TrainingPlanEmployment,
  learningPlan: TrainingPlanRow[],
  otjh: { plannedTotal?: number | null; publishedMinimum?: number | null; rplVolume?: number | null } = {},
  epa: { epao?: string; gatewayReviewDate?: string; epaPeriodFrom?: string; epaPeriodTo?: string } = {},
  contacts: Record<string, any> = {},
  signatures: {
    apprentice?: TrainingPlanSignature;
    employer?: TrainingPlanSignature;
    provider?: TrainingPlanSignature;
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

  const heading = (text: string, size = 15) => {
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
    doc.setDrawColor(210);
    doc.line(MARGIN_X, y, MARGIN_X + CONTENT_W, y);
  };

  // ---- Title ----
  heading('Training Plan', 19);
  paragraph(
    'This document outlines how all three parties (the apprentice, employer, and training provider) will support the achievement of the apprenticeship. It sets out the key milestones of the apprenticeship and the learning plan designed to achieve this, together with the information, policies, procedures and guidance that support it.',
  );
  y += 2;

  doc.setFont('helvetica', 'bold');
  doc.setFontSize(11);
  doc.text(latin1(`Apprentice: ${programme.apprenticeName ?? ''}`), MARGIN_X, y);
  doc.setFont('helvetica', 'normal');
  y += 8;

  // ---- Programme ----
  heading('Apprenticeship Programme Details', 12);
  row('Programme:', programme.programme ?? '');
  row('Apprenticeship Standard/Framework Name:', programme.standard ?? '');
  row('Apprenticeship Reference:', programme.reference ?? '');
  row('Apprenticeship Level:', programme.level ?? '');
  row('Start date of apprenticeship:', fmtDate(programme.startDate));
  row('End date of apprenticeship:', fmtDate(programme.endDate));
  row('Start date of practical period:', fmtDate(programme.practicalStartDate));
  row('Estimated end date of practical period:', fmtDate(programme.practicalEndDate));
  row('Duration of practical period:', programme.durationWeeks != null ? `${programme.durationWeeks} weeks` : '');
  row('ILR Planned Hours:', programme.ilrPlannedHours != null ? String(programme.ilrPlannedHours) : '');
  y += 6;

  // ---- Employment ----
  heading('Employment Details', 12);
  row('Employer:', employment.employerName ?? '');
  row('Delivery Address:', employment.deliveryAddress ?? '');
  row('Apprentice Job Title:', employment.jobTitle ?? '');
  row('Working hours (per week):', employment.workingHoursPerWeek ?? '');
  row('Line Manager:', employment.lineManager ?? '');
  row('Line Manager Title:', employment.lineManagerTitle ?? '');
  row('Start Date With Employer:', fmtDate(employment.startDateWithEmployer));
  y += 6;

  // ---- Commitments ----
  doc.addPage();
  y = MARGIN_TOP;
  heading('Commitment to the Programme', 15);
  COMMITMENTS.forEach((block) => {
    space(16);
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(11);
    doc.text(latin1(block.heading), MARGIN_X, y);
    doc.setFont('helvetica', 'normal');
    y += 5;
    paragraph(block.intro, 9);
    block.items.forEach((item, i) => {
      doc.setFontSize(9);
      const lines = doc.splitTextToSize(latin1(`${i + 1}. ${item}`), CONTENT_W - 4);
      space(lines.length * 4.2 + 1);
      doc.text(lines, MARGIN_X + 3, y);
      y += lines.length * 4.2 + 1.5;
    });
    y += 4;
  });

  // ---- Learning plan ----
  doc.addPage();
  y = MARGIN_TOP;
  heading('Learning Plan', 15);

  // Activity / Method / Delivery Lead / Planned Date / OTJ hours. Each module
  // is a banded heading, its weeks are sub-headings, and the components beneath
  // them are the actual teaching activities.
  const cols = [
    { label: 'Activity/Unit', w: 74 },
    { label: 'Method', w: 34 },
    { label: 'Delivery Lead', w: 38 },
    { label: 'Planned Date', w: 22 },
    { label: 'OTJ (hr)', w: 6 },
  ];

  const headerRow = () => {
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(8.5);
    let x = MARGIN_X;
    cols.forEach((c) => {
      doc.text(doc.splitTextToSize(latin1(c.label), c.w - 2), x, y + 3.5);
      x += c.w;
    });
    doc.setFont('helvetica', 'normal');
    y += 7;
    doc.setDrawColor(120);
    doc.line(MARGIN_X, y, MARGIN_X + CONTENT_W, y);
    y += 1.5;
  };
  headerRow();

  const cellRow = (cells: string[], indent: number) => {
    const wrapped = cells.map((text, i) =>
      doc.splitTextToSize(latin1(text), cols[i].w - 2 - (i === 0 ? indent : 0)),
    );
    const h = Math.max(...wrapped.map((w) => w.length)) * 3.9 + 2.4;
    if (y + h > PAGE_H - MARGIN_BOTTOM) {
      doc.addPage();
      y = MARGIN_TOP;
      headerRow();
      doc.setFontSize(8.5);
    }
    let x = MARGIN_X;
    wrapped.forEach((lines, i) => {
      doc.text(lines, x + (i === 0 ? indent : 0), y + 3.1);
      x += cols[i].w;
    });
    y += h;
    doc.setDrawColor(230);
    doc.line(MARGIN_X, y, MARGIN_X + CONTENT_W, y);
  };

  doc.setFontSize(8.5);
  learningPlan.forEach((module) => {
    // Module band.
    doc.setFont('helvetica', 'bold');
    cellRow(
      [
        module.activity ?? '',
        '',
        module.deliveryLead ?? '',
        fmtDate(module.plannedDate),
        module.plannedOtjHours != null ? String(module.plannedOtjHours) : '',
      ],
      0,
    );
    doc.setFont('helvetica', 'normal');

    const weeks = module.weeks ?? [];
    weeks.forEach((week) => {
      const components = week.components ?? [];
      // A week with no components still appears — an empty week in the
      // curriculum is a real gap, not something to hide.
      doc.setTextColor(90);
      cellRow(
        [
          `Week ${week.weekNumber ?? ''}${week.weekTitle ? ` - ${week.weekTitle}` : ''}`,
          '',
          '',
          '',
          '',
        ],
        3,
      );
      doc.setTextColor(0);

      components.forEach((component) => {
        cellRow(
          [
            component.title ?? '',
            component.method ?? '',
            '',
            '',
            component.otjHours != null ? String(component.otjHours) : '',
          ],
          7,
        );
      });
    });

    if (!weeks.length) {
      doc.setTextColor(120);
      cellRow(['No weeks have been authored for this module yet.', '', '', '', ''], 3);
      doc.setTextColor(0);
    }
  });

  if (!learningPlan.length) {
    paragraph('No learning plan activities have been recorded.', 9);
  }
  y += 6;

  // ---- Off-the-job hours ----
  heading('Off-The-Job Training Hours', 12);
  paragraph(
    'Off-the-job training is training received by the apprentice for the purpose of achieving the knowledge, skills and behaviours of the approved apprenticeship referenced in the apprenticeship agreement. The published figure can only be reduced where there is evidence of relevant prior learning from the apprentice’s initial assessment.',
  );
  row('Planned off-the-job training:', otjh.plannedTotal != null ? String(otjh.plannedTotal) : '');
  row('Published minimum off-the-job training:', otjh.publishedMinimum != null ? String(otjh.publishedMinimum) : '');
  row('Volume of RPL:', otjh.rplVolume != null ? String(otjh.rplVolume) : '');
  y += 6;

  // ---- EPA ----
  heading('End Point Assessment', 12);
  paragraph(
    'End Point Assessment (EPA) is the final assessment of an apprentice at the completion of their apprenticeship, carried out by an independent End Point Assessment Organisation. A Gateway Review determines whether the apprentice is ready to undertake it.',
  );
  row('End Point Assessment Organisation (EPAO):', epa.epao ?? '');
  row('Estimated Gateway Meeting Review Date:', fmtDate(epa.gatewayReviewDate));
  row(
    'EPA Period:',
    epa.epaPeriodFrom || epa.epaPeriodTo
      ? `${fmtDate(epa.epaPeriodFrom)} - ${fmtDate(epa.epaPeriodTo)}`
      : '',
  );

  // ---- Signatures ----
  doc.addPage();
  y = MARGIN_TOP;
  heading('Signatures & Declarations', 15);
  paragraph(
    'By signing this agreement you confirm that you have read, understood and commit to the details, aims and learning plan outlined in this document.',
  );
  y += 2;

  const parties: [string, TrainingPlanSignature | undefined][] = [
    ['Apprentice', signatures.apprentice],
    ['Employer', signatures.employer],
    ['Training Provider', signatures.provider],
  ];
  const colW = CONTENT_W / 3;
  const blockH = 54;
  space(blockH + 4);
  const top = y;

  parties.forEach(([label, sig], i) => {
    const x = MARGIN_X + colW * i;
    doc.setDrawColor(120);
    doc.rect(x, top, colW, blockH);

    doc.setFont('helvetica', 'bold');
    doc.setFontSize(9.5);
    doc.text(latin1(label), x + 2, top + 6);
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(8.5);

    doc.setTextColor(110);
    doc.text('Name:', x + 2, top + 13);
    doc.text('Position:', x + 2, top + 22);
    doc.text('Signature:', x + 2, top + 31);
    doc.text('Date:', x + 2, top + 50);
    doc.setTextColor(0);

    doc.text(doc.splitTextToSize(latin1(sig?.name ?? ''), colW - 20), x + 18, top + 13);
    doc.text(doc.splitTextToSize(latin1(sig?.position ?? ''), colW - 20), x + 18, top + 22);
    doc.text(fmtDate(sig?.signedAt), x + 18, top + 50);

    if (sig?.signature?.startsWith('data:image/')) {
      try {
        doc.addImage(sig.signature, 'PNG', x + 3, top + 33, colW - 6, 13);
      } catch {
        // A corrupt data URL must not lose the whole document.
      }
    }
  });
  y = top + blockH + 8;

  // ---- Appendices ----
  heading('Appendices', 12);
  const apprenticeContact = contacts.apprentice ?? {};
  const employerContact = contacts.employer ?? {};

  doc.setFont('helvetica', 'bold');
  doc.setFontSize(10);
  space(10);
  doc.text('1.1 Apprentice Contact Details', MARGIN_X, y);
  doc.setFont('helvetica', 'normal');
  y += 5;
  row('Name:', apprenticeContact.name ?? '');
  row('Email:', apprenticeContact.email ?? '');
  row('Telephone:', apprenticeContact.telephone ?? '');
  row('Address:', apprenticeContact.address ?? '');
  y += 4;

  doc.setFont('helvetica', 'bold');
  doc.setFontSize(10);
  space(10);
  doc.text('1.2 Employer Contact Details', MARGIN_X, y);
  doc.setFont('helvetica', 'normal');
  y += 5;
  row('Line Manager:', employerContact.lineManager ?? '');
  row('Email:', employerContact.email ?? '');
  row('Telephone:', employerContact.telephone ?? '');
  row('Company:', employerContact.company ?? '');
  row('Address:', employerContact.address ?? '');

  return doc;
}

export function trainingPlanFilename(learnerName: string): string {
  const safe = (learnerName || 'learner').replace(/[^A-Za-z0-9]+/g, '-').replace(/^-|-$/g, '');
  return `Training-Plan-${safe}.pdf`;
}

/** Render an issued Training Plan from its record, with the marks on file. */
export function renderTrainingPlanPdf(document: {
  programme: TrainingPlanProgramme;
  employment: TrainingPlanEmployment;
  learningPlan: TrainingPlanRow[];
  otjh: Record<string, number | null>;
  epa: Record<string, string>;
  contacts: Record<string, any>;
  signatures: {
    apprentice: { signed: boolean; name: string; position: string; signedAt: string | null };
    employer: { signed: boolean; name: string; position: string; signedAt: string | null };
    provider: { signed: boolean; name: string; position: string; signedAt: string | null };
  };
  marks: { apprentice: string; employer: string; provider: string };
}) {
  const pick = (
    state: { signed: boolean; name: string; position: string; signedAt: string | null },
    mark: string,
  ) =>
    state.signed
      ? { name: state.name, position: state.position, signature: mark, signedAt: state.signedAt ?? undefined }
      : undefined;

  return buildTrainingPlanPdf(
    document.programme,
    document.employment,
    document.learningPlan,
    document.otjh,
    document.epa,
    document.contacts,
    {
      apprentice: pick(document.signatures.apprentice, document.marks.apprentice),
      employer: pick(document.signatures.employer, document.marks.employer),
      provider: pick(document.signatures.provider, document.marks.provider),
    },
  );
}
