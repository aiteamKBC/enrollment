// ============================================================================
// Individual Learner Record — printable document
//
// Reproduces the ILR form: Learner details, Health, Education and training,
// Latest employment status, the Extended ILR block (contact preferences, next
// of kin, eligibility, employer, other training, personal circumstances,
// programme understanding, additional information and consents), then the
// learning declaration and the Provider/Sub-contractor declaration.
//
// Signed by the learner and the provider only — an employer has no part in an
// ILR and never sees it.
//
// Values come from the issued document's frozen snapshot, so a signed record
// prints what was signed. Anything we hold no source for renders as an empty
// row for an officer to complete, rather than a guess.
// ============================================================================
import jsPDF from 'jspdf';

const PAGE_W = 210;
const PAGE_H = 297;
const MARGIN_X = 18;
const MARGIN_TOP = 20;
const MARGIN_BOTTOM = 22;
const CONTENT_W = PAGE_W - MARGIN_X * 2;
const LABEL_W = 88;

export interface IlrLearnerDetails {
  learnerReferenceNumber?: string;
  uln?: string;
  familyName?: string;
  givenNames?: string;
  dateOfBirth?: string;
  address1?: string;
  address2?: string;
  address3?: string;
  address4?: string;
  yearsAtAddress?: string;
  telephone?: string;
  email?: string;
  currentPostcode?: string;
  postcodePriorToEnrolment?: string;
  nationalInsuranceNumber?: string;
  sex?: string;
  ethnicity?: string;
  longTermDisability?: boolean | null;
  priorAttainment?: string;
  employmentStatus?: string;
  employmentStartDate?: string;
  dateStatusApplies?: string;
  jobTitle?: string;
  edrsErn?: string;
  selfEmployed?: boolean | null;
  fullTimeEducationPrior?: boolean | null;
  contractedHoursPerWeek?: string;
  isSmallEmployer?: boolean | null;
}

export interface IlrSignature {
  name?: string;
  signature?: string;
  signedAt?: string | null;
}

/** jsPDF's built-in fonts are Latin-1 only; map what this form produces. */
function latin1(text: unknown): string {
  return String(text ?? '')
    .replace(/[–—]/g, '-')
    .replace(/[‘’]/g, "'")
    .replace(/[“”]/g, '"')
    .replace(/…/g, '...')
    .replace(/£/g, 'GBP ')
    .replace(/€/g, 'EUR ')
    .replace(/[Ā-\u{10FFFF}]/gu, '?');
}

function fmtDate(iso?: string | null): string {
  if (!iso) return '';
  const m = String(iso).match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? `${m[3]}/${m[2]}/${m[1]}` : String(iso);
}

type Row =
  | { kind: 'heading'; text: string }
  | { kind: 'section'; text: string }
  | { kind: 'text'; label: string; value: string }
  | { kind: 'yesno'; label: string; value: boolean | null | undefined }
  | { kind: 'note'; text: string };

export function buildIlrPdf(
  details: IlrLearnerDetails,
  answers: Record<string, any>,
  signatures: { learner?: IlrSignature; provider?: IlrSignature } = {},
): jsPDF {
  const doc = new jsPDF({ unit: 'mm', format: 'a4' });
  doc.setFont('helvetica', 'normal');

  const a = answers ?? {};
  const contact = a.contact ?? {};
  const kin = a.nextOfKin ?? {};
  const elig = a.eligibility ?? {};
  const employer = a.employer ?? {};
  const other = a.otherTraining ?? {};
  const circ = a.circumstances ?? {};
  const und = a.understanding ?? {};
  const add = a.additional ?? {};
  const media = a.media ?? {};
  const decl = a.declarations ?? {};

  const rows: Row[] = [
    { kind: 'heading', text: 'Individual Learner Record' },
    { kind: 'section', text: 'Learner details' },
    { kind: 'text', label: 'Learner reference number', value: details.learnerReferenceNumber ?? '' },
    { kind: 'text', label: 'ULN', value: details.uln ?? '' },
    { kind: 'text', label: 'Family name', value: details.familyName ?? '' },
    { kind: 'text', label: 'Given names', value: details.givenNames ?? '' },
    { kind: 'text', label: 'Date of birth', value: fmtDate(details.dateOfBirth) },
    { kind: 'text', label: 'Address 1', value: details.address1 ?? '' },
    { kind: 'text', label: 'Address 2', value: details.address2 ?? '' },
    { kind: 'text', label: 'Address 3', value: details.address3 ?? '' },
    { kind: 'text', label: 'Address 4', value: details.address4 ?? '' },
    { kind: 'text', label: 'How long have you been at this address (years)?', value: details.yearsAtAddress ?? '' },
    { kind: 'text', label: 'Telephone number', value: details.telephone ?? '' },
    { kind: 'text', label: 'Current post code', value: details.currentPostcode ?? '' },
    { kind: 'text', label: 'Postcode prior to enrolment', value: details.postcodePriorToEnrolment ?? '' },
    { kind: 'text', label: 'National insurance number', value: details.nationalInsuranceNumber ?? '' },
    { kind: 'text', label: 'Sex', value: details.sex ?? '' },
    { kind: 'text', label: 'Ethnicity', value: details.ethnicity ?? '' },

    { kind: 'section', text: 'Health' },
    {
      kind: 'yesno',
      label: 'Do you consider yourself to have a long term disability, health problem or any learning difficulties?',
      value: details.longTermDisability,
    },

    { kind: 'section', text: 'Education and training' },
    { kind: 'text', label: 'Prior attainment', value: details.priorAttainment ?? '' },

    { kind: 'section', text: 'Latest employment status details' },
    { kind: 'text', label: 'Status', value: details.employmentStatus ?? '' },
    { kind: 'text', label: 'Employment start date', value: fmtDate(details.employmentStartDate) },
    { kind: 'text', label: 'Date status applies', value: fmtDate(details.dateStatusApplies) },
    { kind: 'text', label: 'Job title', value: details.jobTitle ?? '' },
    { kind: 'text', label: 'EDRS/ERN number', value: details.edrsErn ?? '' },
    { kind: 'yesno', label: 'Self employed', value: details.selfEmployed },
    { kind: 'yesno', label: 'Full time education or training prior to enrolment', value: details.fullTimeEducationPrior },
    { kind: 'text', label: 'Contracted hours per week', value: details.contractedHoursPerWeek ?? '' },
    { kind: 'yesno', label: 'Is small employer', value: details.isSmallEmployer },

    { kind: 'heading', text: 'Extended ILR' },
    { kind: 'section', text: 'Contact Preferences' },
    { kind: 'yesno', label: 'By post', value: contact.byPost },
    { kind: 'yesno', label: 'By phone', value: contact.byPhone },
    { kind: 'yesno', label: 'By e-mail', value: contact.byEmail },

    { kind: 'section', text: 'Emergency contact details/Next of kin' },
    { kind: 'text', label: 'Full name', value: kin.fullName ?? '' },
    { kind: 'text', label: 'Relationship to', value: kin.relationship ?? '' },
    { kind: 'text', label: 'Email address', value: kin.email ?? '' },
    { kind: 'text', label: 'Phone number', value: kin.phone ?? '' },
    { kind: 'text', label: 'Contact post code', value: kin.postcode ?? '' },
    { kind: 'text', label: 'Contact address', value: kin.address ?? '' },

    { kind: 'section', text: 'Eligibility' },
    { kind: 'yesno', label: 'Are you primarily employed in England?', value: elig.employedInEngland },
    { kind: 'yesno', label: 'Are you a UK/EEA National?', value: elig.ukEeaNational },
    { kind: 'text', label: 'Nationality', value: elig.nationality ?? '' },
    { kind: 'yesno', label: 'Have you been resident in the UK/EEA for the previous 3 years?', value: elig.residentPrevious3Years },
    { kind: 'text', label: 'How many full years have you lived in the UK?', value: elig.yearsInUk != null ? String(elig.yearsInUk) : '' },
    { kind: 'yesno', label: 'Do you require a Work Permit?', value: elig.requiresWorkPermit },
    { kind: 'text', label: 'Country of residence', value: elig.countryOfResidence ?? '' },

    { kind: 'section', text: 'Employer Details' },
    { kind: 'text', label: 'Organisation name', value: employer.organisationName ?? '' },
    { kind: 'text', label: 'Organisation post code', value: employer.postcode ?? '' },
    { kind: 'text', label: 'Organisation address', value: employer.address ?? '' },
    { kind: 'text', label: 'Organisation city', value: employer.city ?? '' },
    { kind: 'text', label: 'Line Manager name', value: employer.lineManagerName ?? '' },
    { kind: 'text', label: 'Line Manager email', value: employer.lineManagerEmail ?? '' },
    { kind: 'text', label: 'Line Manager phone', value: employer.lineManagerPhone ?? '' },

    { kind: 'section', text: 'Other training' },
    {
      kind: 'yesno',
      label: 'Have you attended any other government funded training programmes in the last 12 months?',
      value: other.attended12m,
    },
    { kind: 'text', label: 'When was it completed?', value: other.completedWhen ?? '' },

    { kind: 'section', text: 'Personal Circumstances' },
    { kind: 'text', label: 'Do you have any caring responsibilities?', value: circ.caringResponsibilities ?? '' },
    { kind: 'text', label: 'Are there any other personal circumstances you want to tell us about?', value: circ.other ?? '' },
    { kind: 'yesno', label: 'Care leaver', value: circ.careLeaver },

    { kind: 'section', text: 'Programme understanding' },
    { kind: 'text', label: 'What is your understanding of the programme you are applying for?', value: und.programmeUnderstanding ?? '' },
    {
      kind: 'text',
      label: 'How will this programme help you in your career development/aspirations and/or with your progression?',
      value: und.careerProgression ?? '',
    },

    { kind: 'section', text: 'Additional information' },
    { kind: 'yesno', label: 'Are you aged between 16 and 18?', value: add.aged16to18 },
    { kind: 'yesno', label: 'Are you aged between 19 and 24?', value: add.aged19to24 },
    { kind: 'text', label: 'Media Consent', value: '' },
    { kind: 'yesno', label: 'Do you give Kent Business College consent for the above?', value: media.consent },
    {
      kind: 'yesno',
      label: 'I understand that my Personal Learning Record (PLR) information will be shared with Kent Business College and other relevant organisations',
      value: decl.plrShared,
    },
    {
      kind: 'yesno',
      label: 'I understand that I am on programme that is part funded by the DfE. I understand that members of the qualification and funding authorities may contact me in connection to my apprenticeship',
      value: decl.dfeContact,
    },
    {
      kind: 'yesno',
      label: 'I understand that relevant personal details will be provided to the End Point and Awarding Organisation so that Registration and Certification can take place',
      value: decl.epaoDetails,
    },
    {
      kind: 'yesno',
      label: 'I understand that Kentbusinesscollege will hold any relevant copies of my certificates for audit purposes',
      value: decl.kbcHoldsCerts,
    },
    { kind: 'yesno', label: 'I confirm that all the information contained in this application is accurate and true', value: decl.infoAccurate },
    {
      kind: 'yesno',
      label: 'Could you confirm whether you expect to spend more than 50% of your working hours in England?',
      value: decl.over50PercentEngland,
    },
    { kind: 'text', label: 'Please confirm your "current wage rate per hour" is equal to or higher than:', value: decl.wageRateBand ?? '' },
    { kind: 'yesno', label: 'Have you ever been known by any other name?', value: decl.knownByOtherName },
    {
      kind: 'yesno',
      label: 'Please confirm that you are aware your training provider will need to access your PLR:',
      value: decl.plrAccessAware,
    },
  ];

  // ---- layout ----
  let y = MARGIN_TOP;

  const ensureSpace = (needed: number) => {
    if (y + needed > PAGE_H - MARGIN_BOTTOM) {
      doc.addPage();
      y = MARGIN_TOP;
    }
  };

  const rule = () => {
    doc.setDrawColor(200);
    doc.line(MARGIN_X, y, MARGIN_X + CONTENT_W, y);
  };

  for (const row of rows) {
    if (row.kind === 'heading') {
      ensureSpace(16);
      y += 4;
      doc.setFont('helvetica', 'bold');
      doc.setFontSize(17);
      doc.text(latin1(row.text), MARGIN_X, y);
      doc.setFont('helvetica', 'normal');
      y += 8;
      continue;
    }
    if (row.kind === 'section') {
      ensureSpace(14);
      y += 3;
      doc.setFont('helvetica', 'bold');
      doc.setFontSize(12);
      doc.text(latin1(row.text), MARGIN_X, y);
      doc.setFont('helvetica', 'normal');
      y += 3;
      rule();
      y += 4;
      continue;
    }
    if (row.kind === 'note') {
      ensureSpace(10);
      doc.setFontSize(9);
      const lines = doc.splitTextToSize(latin1(row.text), CONTENT_W);
      doc.text(lines, MARGIN_X, y);
      y += lines.length * 4.4 + 2;
      continue;
    }

    // label / value row
    doc.setFontSize(9.5);
    const labelLines = doc.splitTextToSize(latin1(row.label), LABEL_W - 4);
    const valueText = row.kind === 'yesno' ? '' : latin1(row.value);
    const valueLines = valueText
      ? doc.splitTextToSize(valueText, CONTENT_W - LABEL_W - 4)
      : [''];
    const height = Math.max(labelLines.length, valueLines.length) * 4.6 + 3;

    ensureSpace(height + 2);
    doc.setTextColor(110);
    doc.text(labelLines, MARGIN_X, y + 3.4);
    doc.setTextColor(0);

    if (row.kind === 'yesno') {
      // Both options printed with the chosen one ticked, as the form does.
      const x = MARGIN_X + LABEL_W;
      doc.text('Yes', x, y + 3.4);
      doc.rect(x + 7, y + 0.6, 3.6, 3.6);
      doc.text('No', x + 15, y + 3.4);
      doc.rect(x + 21, y + 0.6, 3.6, 3.6);
      if (row.value === true) doc.text('X', x + 7.9, y + 3.5);
      if (row.value === false) doc.text('X', x + 21.9, y + 3.5);
    } else {
      doc.text(valueLines, MARGIN_X + LABEL_W, y + 3.4);
    }

    y += height;
    rule();
  }

  // ---- declarations + signatures ----
  ensureSpace(70);
  y += 6;
  doc.setFont('helvetica', 'bold');
  doc.setFontSize(11);
  doc.text('Learning declaration', MARGIN_X, y);
  doc.setFont('helvetica', 'normal');
  y += 5;

  doc.setFontSize(9);
  [
    'I confirm my agreement to share my Personal Learning Record (PLR) information with Kent Business College and other relevant bodies.',
    'I understand that my programme is supported and funded by the Department of Education (DfE) on behalf of the Secretary of State. I am happy to be contacted by the relevant funding authorities in connection with this programme.',
    'I confirm all the information completed in this Application for learning document is accurate.',
  ].forEach((paragraph) => {
    const lines = doc.splitTextToSize(latin1(paragraph), CONTENT_W);
    ensureSpace(lines.length * 4.2 + 3);
    doc.text(lines, MARGIN_X, y);
    y += lines.length * 4.2 + 3;
  });

  y += 2;
  y = signatureBlock(doc, y, 'Learner', details, signatures.learner);

  ensureSpace(40);
  y += 6;
  doc.setFont('helvetica', 'bold');
  doc.setFontSize(11);
  doc.text('Provider/Sub-contractor Declaration', MARGIN_X, y);
  doc.setFont('helvetica', 'normal');
  y += 5;
  doc.setFontSize(9);
  const providerNote = doc.splitTextToSize(
    latin1(
      'I confirm I have seen evidence to verify the learners identity, immigration permission (if applicable) and relevant eligibility for this qualification/funding',
    ),
    CONTENT_W,
  );
  doc.text(providerNote, MARGIN_X, y);
  y += providerNote.length * 4.2 + 3;
  signatureBlock(doc, y, 'Provider', undefined, signatures.provider);

  return doc;
}

/** Name / drawn mark / date, as the form's signature table. */
function signatureBlock(
  doc: jsPDF,
  y: number,
  party: 'Learner' | 'Provider',
  details: IlrLearnerDetails | undefined,
  sig?: IlrSignature,
): number {
  const height = 24;
  const nameW = 42;
  const markW = CONTENT_W - nameW - 42;

  doc.setDrawColor(120);
  doc.rect(MARGIN_X, y, nameW, height);
  doc.rect(MARGIN_X + nameW, y, markW, height);
  doc.rect(MARGIN_X + nameW + markW, y, 20, height);
  doc.rect(MARGIN_X + nameW + markW + 20, y, 22, height);

  doc.setFontSize(9);
  const printedName =
    sig?.name ||
    (details ? [details.givenNames, details.familyName].filter(Boolean).join(' ') : '');
  doc.text(latin1(party === 'Learner' ? 'Print name' : 'Print Name'), MARGIN_X + 2, y + 5);
  doc.text(latin1(printedName), MARGIN_X + 2, y + 11);
  doc.text('Signature', MARGIN_X + 2, y + 19);
  doc.text('Date', MARGIN_X + nameW + markW + 2, y + height / 2 + 1);
  doc.text(fmtDate(sig?.signedAt), MARGIN_X + nameW + markW + 22, y + height / 2 + 1);

  if (sig?.signature?.startsWith('data:image/')) {
    try {
      doc.addImage(sig.signature, 'PNG', MARGIN_X + nameW + 4, y + 3, 48, height - 6);
    } catch {
      // A corrupt data URL must not lose the whole document.
    }
  }
  return y + height;
}

export function ilrFilename(learnerName: string): string {
  const safe = (learnerName || 'learner').replace(/[^A-Za-z0-9]+/g, '-').replace(/^-|-$/g, '');
  return `Individual-Learner-Record-${safe}.pdf`;
}

/** Render an issued ILR from its record, with the marks on file. */
export function renderIlrPdf(document: {
  learnerDetails: IlrLearnerDetails;
  answers: Record<string, any>;
  signatures: {
    learner: { signed: boolean; name: string; signedAt: string | null };
    provider: { signed: boolean; name: string; signedAt: string | null };
  };
  marks: { learner: string; provider: string };
}) {
  const { learnerDetails, answers, signatures, marks } = document;
  return buildIlrPdf(learnerDetails, answers, {
    learner: signatures.learner.signed
      ? { name: signatures.learner.name, signature: marks.learner, signedAt: signatures.learner.signedAt ?? undefined }
      : undefined,
    provider: signatures.provider.signed
      ? { name: signatures.provider.name, signature: marks.provider, signedAt: signatures.provider.signedAt ?? undefined }
      : undefined,
  });
}
