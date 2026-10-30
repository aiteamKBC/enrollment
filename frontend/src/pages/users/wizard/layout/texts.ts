/**
 * Every piece of built-in wording in the enrolment wizard that the builder can
 * edit, keyed by slot and attached to the layout item it belongs to.
 *
 * `default` is the wording the wizard has always shown; an edit published from
 * the builder (layout.texts[key]) replaces it. Slots are listed per item in the
 * order they appear on the page, which is the order the builder shows them in.
 *
 *   line      — one line of plain text (a label, a heading)
 *   paragraph — plain text, may run to several lines (notes, help text)
 *   rich      — formatted text (see richFormat.ts): headings, bold, italic,
 *               lists and links
 *
 * Wording printed on the Extended ILR PDF is never `rich`: the PDF prints plain
 * text, and prints the edited wording so it matches what the learner was asked.
 */
import { CvJobText } from '../steps/cvJobText';
import { IlrText } from '../steps/ilrAdditionalText';

export type TextKind = 'line' | 'paragraph' | 'rich';

export interface TextSlot {
  key: string;
  /** The layout item (or `step:<slug>`) the wording belongs to. */
  item: string;
  /** How the builder names the slot. */
  label: string;
  kind: TextKind;
  default: string;
  /** Builder hint shown under the editor. */
  hint?: string;
}

type Def = [key: string, label: string, kind: TextKind, def: string, hint?: string];

const INTRODUCTION = `## Shaping Tomorrow’s Business Leaders
Kent Business College offers world-class education in Project Management, Marketing, and Leadership to prepare you for success in today’s competitive business landscape.

## What is an Apprenticeship?
*The term ‘apprenticeship’ simply means learning while working, gaining practical skills and knowledge on the job, regardless of your age, background, or current job title. It is meant to support upskilling within the British workforce employed by British organisations. Apprenticeship does not mean being young, in a junior role, or earning a low income. In the UK, anyone working legitimately can take part in an apprenticeship.*

**The programme is structured to support your development each week with:**
- Interactive online sessions
- Programme materials and quizzes (focused on knowledge)
- Coach-guided assignments to strengthen your practice and writing.

**We call this developing the “habits of success”. That means:**
- Attending one online class a week
- Reading 5–10 pages a week
- Reflecting on your work and assignments

Doing this consistently builds not only your skills and understanding, but the mindset and momentum to succeed both in the apprenticeship and beyond.`;

const BEFORE_YOU_BEGIN = `## The Onboarding Stage
During this stage, you will be required to provide personal details and information regarding your qualifications. The purpose of this application is to help us confirm your eligibility for the apprenticeship funding.

## Eligibility for Apprenticeship Funding
Eligibility is determined by strict guidelines set forth by the UK Government’s Department for Education (DfE). Therefore, every step of the application process must be completed accurately and thoroughly to be considered for funding and successful enrolment in the programme.

## Supporting Evidence
**Documents you may need to include:**
- Valid proof of identification and residency status
- Evidence of your right to work in the UK (England only) for the full duration of the apprenticeship
- GCSE English certificate or an approved equivalent **(if applicable)**
- GCSE Maths certificate or an approved equivalent **(if applicable)**
- Professional qualification certificates **(if applicable)**
- Degree certificates and transcripts **(if applicable)**

Instructions on how to upload these documents will be provided within the application form.

## Application Guidance
If you exit the application before completing and submitting it, your progress will be saved.

**Please note: You will not be able to submit the form or proceed with your application unless all required fields are completed and all supporting documents are uploaded.** *Incomplete applications or missing documentation may result in delays in processing your request.*

## Application Review
Once submitted, the compliance team will review your application.
If you have any questions, feel free to email us at: [office@kentbusinesscollege.org](mailto:office@kentbusinesscollege.org)

**Good luck with your application!**`;

const NEXT_STEPS = `## Next Step: Book Your Compliance Meeting
The next step in your apprenticeship application process is to **book a compliance meeting as soon as possible**. This meeting is a key part of your onboarding and must be **attended by both you and your line manager**. Book a meeting using the following link:

[Book Compliance Meeting](https://outlook.office.com/book/StudentSupport1@kentbusinesscollege.com/s/EmOovIV5H0GGd131KDuFJQ2) (select “Compliance Meeting”)

## What Needs to Be Completed Before the Meeting?
**Before attending your compliance meeting, please ensure you have:**
- Ensured your employer has signed the apprenticeship agreement
- Asked your employer to add Kent Business College to the Digital Apprenticeship Service (DAS)
- Completed the initial Maths and English assessments at least 24 hours before the compliance meeting appointment (this is essential)

**Thank you!** Please don’t hesitate to reach out if you have any questions: [office@kentbusinesscollege.com](mailto:office@kentbusinesscollege.com)`;

const PRIVACY_NOTICE = `**Training providers should ensure that all learners have seen this privacy notice as part of their enrolment process.**

This privacy notice is issued on behalf of the Secretary of State for the Department of Education (DfE) to inform learners about the Individualised Learner Record (ILR) and how their personal information is used in the ILR. Your personal information is used by the DfE to exercise our functions under article 6(1)(e) of the UK GDPR and to meet our statutory responsibilities, including under the Apprenticeships, Skills, Children and Learning Act 2009. Our lawful basis for using your special category personal data is covered under Substantial Public Interest based in law (Article 9(2)(g)) of UK GDPR legislation. This processing is under Section 54 of the Further and Higher Education Act (1992).

The ILR collects data about learners and learning undertaken. Publicly funded colleges, training organisations, local authorities, and employers (FE providers) must collect and return the data each year under the terms of a funding agreement, contract or grant agreement. It helps ensure that public money is being spent in line with government targets. It is also used for education, training, employment, and well-being purposes, including research.

We retain your ILR learner data for 20 years for operational purposes (e.g. to fund your learning and to publish official statistics). Your personal data is then retained in our research databases until you are aged 80 years so that it can be used for long-term research purposes. For more information about the ILR and the data collected, please see the ILR specification at [https://www.gov.uk/government/collections/individualised-learner-record-ilr](https://www.gov.uk/government/collections/individualised-learner-record-ilr)

ILR data is shared with third parties where it complies with DfE data sharing procedures and where the law allows it. The DfE and the English European Social Fund (ESF) Managing Authority (or agents acting on their behalf) may contact learners to carry out research and evaluation to inform the effectiveness of training.

For more information about how your personal data is used and your individual rights, please see the DfE Personal Information Charter [(https://www.gov.uk/government/organisations/department-for-education/about/personal-information-charter)](https://www.gov.uk/government/organisations/department-for-education/about/personal-information-charter) and the DfE Privacy Notice [(https://www.gov.uk/government/publications/privacy-notice-for-key-stage-5-and-adult-education)](https://www.gov.uk/government/publications/privacy-notice-for-key-stage-5-and-adult-education)

If you would like to get in touch with us or request a copy of the personal information DfE holds about you, you can contact the DfE in the following ways:
- Using our online contact form [https://form.education.gov.uk/service/Contact_the_Department_for_Education](https://form.education.gov.uk/service/Contact_the_Department_for_Education)
- By telephoning the DfE Helpline on 0370 000 2288
- Or in writing to: Data Protection Officer, Department for Education, 4th floor, 2 St Paul’s Place, 125 Norfolk Street, Sheffield, S1 2FJ

If you are unhappy with how we have used your personal data, you can complain to the Information Commissioner’s Office (ICO) at: Wycliffe House, Water Lane, Wilmslow, Cheshire, SK9 5AF. You can also call their helpline on 0303 123 1113 or visit [https://www.ico.org.uk](https://www.ico.org.uk)

Date last updated: January 2026`;

const COLLEGE_DECLARATION = `**How we use your personal information**
The information I have given on this form is correct. I agree that my admission as a student to Kent Business College is subject to regulations. I agree to provide any additional information which may be required and to inform the training provider of any alteration to the information provided. I agree to Kent Business College storing, processing and sharing (with external parties associated with the learning programme detailed in this agreement) personal data contained in this form, or other data including electronic photographic images, which the training provider may obtain from me or other people, whilst I am a student. I agree to being contacted by SMS/Email and to the processing of data for any purposes connected with my studies or my health and safety whilst on the premises or for any other legitimate reason. I agree to conduct myself at all times in a manner which upholds the good reputation of the training provider and which does not obstruct the administration and work of the training provider, of the learning or enjoyment of its students and to abide at all times by all Kent Business College values, rules, regulations, policies and procedures.`;

const EVIDENCE_INSTRUCTIONS = `Please upload a copy of your proof of identification and residency using the ‘Add evidence’ button below. In the text box, please provide written information about the evidence provided (for example, passport, Application Registration Card).

For UK nationals: valid proof of identification and residency — passport or birth certificate. For non-UK nationals: passport or birth certificate for identification, and for residency a valid visa, Home Office letter, Immigration and Nationality Department letter or Application Registration Card (ARC), with the start of UK residency 3 years prior to the enrolment date. For EEA nationals: proof of pre-settled or settled status under the EU Settlement Scheme.`;

const FIELD_HINT = 'The standard wording names the learner’s programme field (marketing, project management…). Write {field} where the field should go; it reads “your programme” for other programmes.';

const SLOTS: Record<string, Def[]> = {
  // ── step subtitles ──
  'step:ilr-details': [['step.ilr-details.subtitle', 'Subtitle', 'line', 'Individualised Learner Record']],
  'step:ilr': [['step.ilr.subtitle', 'Subtitle', 'line', 'Learner Details Data Capture Form']],

  // ── blocks ──
  'block.introduction': [
    ['block.introduction.title', 'Heading', 'line', 'Welcome'],
    ['block.introduction.body', 'Page text', 'rich', INTRODUCTION],
  ],
  'block.beforeYouBegin': [
    ['block.beforeYouBegin.title', 'Heading', 'line', 'Before You Begin'],
    ['block.beforeYouBegin.body', 'Page text', 'rich', BEFORE_YOU_BEGIN],
  ],
  'block.nextSteps': [
    ['block.nextSteps.title', 'Heading', 'line', 'What Happens Now?'],
    ['block.nextSteps.body', 'Page text', 'rich', NEXT_STEPS],
  ],
  'block.plr': [
    ['block.plr.title', 'Heading', 'line', 'Personal Learning Record'],
    ['block.plr.empty', 'Shown when no records are added', 'line', 'There are no personal learning records.'],
  ],
  'block.skillsRadar': [
    ['block.skillsRadar.title', 'Heading', 'line', 'Skills Radar'],
    ['block.skillsRadar.instructions', 'Instructions', 'paragraph', 'Rate yourself on each item. You can revisit any answer before submitting.'],
    ['block.skillsRadar.choose', 'Question prompt', 'line', 'Choose one answer that most applies to you:'],
    ['block.skillsRadar.note', 'Note label', 'line', 'Add a note'],
    ['block.skillsRadar.upload', 'Evidence label', 'line', 'Upload evidence:'],
  ],
  'block.inclusion': [
    ['block.inclusion.title', 'Heading', 'line', 'Inclusion'],
    ['block.inclusion.body', 'Instructions', 'paragraph', 'Before you read our policies, please complete the inclusion assessment. It helps us understand any support you may need during your apprenticeship. It opens on our Safeguarding site in a new tab; when you have finished, come back to this tab to continue.'],
    ['block.inclusion.start', 'Button', 'line', 'Start inclusion assessment'],
    ['block.inclusion.done', 'Shown once completed', 'line', 'Thank you, we have received your inclusion assessment.'],
  ],
  'block.policies': [
    ['block.policies.title', 'Heading', 'line', 'Documents'],
    ['block.policies.group', 'Group heading', 'line', 'Kent Business College'],
    ['block.policies.acknowledge', 'Acknowledgement', 'line', 'I have read and understood this document'],
  ],

  // ── Personal Details ──
  'pd.firstName': [['pd.firstName.label', 'Label', 'line', 'First Name']],
  'pd.lastName': [['pd.lastName.label', 'Label', 'line', 'Last Name']],
  'pd.email': [['pd.email.label', 'Label', 'line', 'Email']],
  'pd.phone': [['pd.phone.label', 'Label', 'line', 'Phone']],
  'pd.address': [['pd.address.label', 'Label', 'line', 'Address']],
  'pd.dob': [
    ['pd.dob.label', 'Label', 'line', 'Date of Birth'],
    ['pd.dob.ageLabel', 'Age label', 'line', 'Age'],
    ['pd.dob.ageHelp', 'Age help text', 'line', 'Calculated from your date of birth'],
  ],
  'pd.sex': [['pd.sex.label', 'Label', 'line', 'Sex']],
  'pd.signature': [
    ['pd.signature.label', 'Label', 'line', 'Your signature'],
    ['pd.signature.help', 'Help text', 'paragraph', 'Draw your signature or upload an image of an existing one. It is saved against your record and used on your enrolment paperwork.'],
  ],

  // ── ILR (learner details) ──
  'ild.h.learner': [
    ['ild.h.learner.title', 'Section heading', 'line', 'Learner Details'],
    ['ild.h.learner.intro', 'Section intro', 'paragraph', 'The greyed-out details come from your learner record. If any of them are wrong, please tell the enrolment team.'],
  ],
  'ild.record': [
    ['ild.record.familyName', 'Family name label', 'line', 'Family name'],
    ['ild.record.givenNames', 'Given names label', 'line', 'Given names'],
    ['ild.record.dateOfBirth', 'Date of birth label', 'line', 'Date of birth'],
    ['ild.record.currentPostcode', 'Current postcode label', 'line', 'Current postcode'],
    ['ild.record.address1', 'Address line 1 label', 'line', 'Current address line 1'],
    ['ild.record.address2', 'Address line 2 label', 'line', 'Current address line 2'],
    ['ild.record.address3', 'Address line 3 label', 'line', 'Current address line 3'],
    ['ild.record.address4', 'Address line 4 label', 'line', 'Current address line 4'],
  ],
  'ild.yearsAtAddress': [
    ['ild.yearsAtAddress.label', 'Label', 'line', 'How long have you been at this address (years)?'],
    ['ild.yearsAtAddress.sinceBirth', 'Checkbox', 'line', 'Since birth'],
  ],
  'ild.telephone': [['ild.telephone.label', 'Label', 'line', 'Telephone number']],
  'ild.postcodePrior': [['ild.postcodePrior.label', 'Label', 'line', 'Postcode prior to enrolment']],
  'ild.ni': [
    ['ild.ni.label', 'Label', 'line', 'National Insurance number'],
    ['ild.ni.help', 'Help when there is no NI number', 'paragraph', 'If you do not have a National Insurance Number, you need to apply for one by phoning: Telephone: 0800 141 2075 | Textphone: 0800 141 2438'],
    ['ild.ni.confirm', 'Confirmation label', 'line', 'Please confirm that you have done so by ticking the box to the right.'],
  ],
  'ild.email': [['ild.email.label', 'Label', 'line', 'Email address']],
  'ild.legalSex': [['ild.legalSex.label', 'Label', 'line', 'Legal Sex']],
  'ild.pronouns': [['ild.pronouns.label', 'Label', 'line', 'What pronouns do you use?']],
  'ild.ethnicity': [['ild.ethnicity.label', 'Label', 'line', 'Ethnicity']],
  'ild.h.health': [['ild.h.health.title', 'Section heading', 'line', 'Health']],
  'ild.disability': [['ild.disability.label', 'Question', 'line', 'Do you consider yourself to have a long-term disability, health problem or any learning difficulties?']],
  'ild.h.education': [['ild.h.education.title', 'Section heading', 'line', 'Education level']],
  'ild.highestQualification': [['ild.highestQualification.label', 'Question', 'line', 'Please select from the drop-down list the highest-level qualification you have achieved to date.']],
  'ild.h.employment': [['ild.h.employment.title', 'Section heading', 'line', 'Employment Status']],
  'ild.employmentStatus': [
    ['ild.employmentStatus.label', 'Label', 'line', 'Status'],
    ['ild.employmentStatus.startDate', 'Start date label', 'line', 'Start date with Employer'],
    ['ild.employmentStatus.jobTitle', 'Job title label', 'line', 'Job title'],
    ['ild.employmentStatus.selfEmployed', 'Self-employed question', 'line', 'Are you self employed?'],
  ],
  'ild.fullTimeEducation': [
    ['ild.fullTimeEducation.label', 'Question', 'line', 'Are you currently in full-time education or training?'],
    ['ild.fullTimeEducation.leavingDate', 'Leaving date label', 'line', 'Please provide expected leaving date'],
    ['ild.fullTimeEducation.unemployment', 'Length of unemployment label', 'line', 'Length of unemployment'],
    ['ild.fullTimeEducation.volunteer', 'Volunteering question', 'line', 'Do you volunteer?'],
  ],
  'ild.stateBenefits': [
    ['ild.stateBenefits.label', 'Question', 'line', 'If you are in receipt of any additional state benefits please select the correct option from the drop down menu'],
    ['ild.stateBenefits.claimBasis', 'Claim question', 'line', 'Please confirm this is in your own right or as part of a joint claim (not as a dependant)'],
  ],
  'ild.h.declaration': [['ild.h.declaration.title', 'Section heading', 'line', 'Declaration']],
  'ild.declaration': [
    ['ild.declaration.privacy', 'DfE privacy notice', 'rich', PRIVACY_NOTICE, 'Statutory DfE wording — change only when the DfE revises it.'],
    ['ild.declaration.college', 'College declaration', 'rich', COLLEGE_DECLARATION],
  ],
  'ild.signature': [
    ['ild.signature.certify', 'Certification', 'line', 'I certify that the information contained on this form is correct'],
    ['ild.signature.label', 'Signature label', 'line', 'Learner signature'],
  ],

  // ── Extended ILR ──
  'xilr.h.contact': [
    ['xilr.h.contact.title', 'Section heading', 'line', 'Contact Preferences'],
    ['xilr.h.contact.intro', 'Section intro', 'line', 'How would you prefer to be contacted?'],
  ],
  'xilr.contactPost': [['xilr.contactPost.label', 'Question', 'line', 'By post']],
  'xilr.contactPhone': [['xilr.contactPhone.label', 'Question', 'line', 'By phone']],
  'xilr.contactEmail': [['xilr.contactEmail.label', 'Question', 'line', 'By e-mail']],
  'xilr.h.nextOfKin': [['xilr.h.nextOfKin.title', 'Section heading', 'line', 'Emergency contact details / Next of kin']],
  'xilr.nokName': [['xilr.nokName.label', 'Label', 'line', 'Full name']],
  'xilr.nokRelationship': [['xilr.nokRelationship.label', 'Label', 'line', 'Relationship to you']],
  'xilr.nokEmail': [['xilr.nokEmail.label', 'Label', 'line', 'Email address']],
  'xilr.nokPhone': [['xilr.nokPhone.label', 'Label', 'line', 'Phone number']],
  'xilr.nokSameAddress': [
    ['xilr.nokSameAddress.label', 'Question', 'line', 'Address same as learner?'],
    ['xilr.nokSameAddress.postcode', 'Postcode label', 'line', 'Postcode'],
    ['xilr.nokSameAddress.address', 'Address label', 'line', 'Address'],
  ],
  'xilr.h.eligibility': [['xilr.h.eligibility.title', 'Section heading', 'line', 'Eligibility']],
  'xilr.employedInEngland': [['xilr.employedInEngland.label', 'Question', 'line', 'Are you primarily employed in England?']],
  'xilr.countryOfResidence': [['xilr.countryOfResidence.label', 'Question', 'line', 'Please state your country of residence']],
  'xilr.ukEeaNational': [['xilr.ukEeaNational.label', 'Question', 'line', 'Are you a UK/EEA National?']],
  'xilr.nationality': [['xilr.nationality.label', 'Question', 'line', 'Please state your nationality']],
  'xilr.resident3Years': [['xilr.resident3Years.label', 'Question', 'line', 'Have you been resident in the UK/EEA for the previous 3 years?']],
  'xilr.yearsInUk': [['xilr.yearsInUk.label', 'Question', 'line', 'How many full years have you lived in the UK?']],
  'xilr.workPermit': [['xilr.workPermit.label', 'Question', 'line', 'Do you require a Work Permit?']],
  'xilr.evidence': [
    ['xilr.evidence.instructions', 'Instructions', 'rich', EVIDENCE_INSTRUCTIONS],
    ['xilr.evidence.placeholder', 'Text box placeholder', 'line', 'Describe the evidence provided…'],
  ],
  'xilr.h.employer': [
    ['xilr.h.employer.title', 'Section heading', 'line', 'Employer Details'],
    ['xilr.h.employer.intro', 'Section intro', 'line', 'Please provide the address you work at:'],
  ],
  'xilr.employerName': [['xilr.employerName.label', 'Label', 'line', 'Organisation Name']],
  'xilr.employerPostcode': [['xilr.employerPostcode.label', 'Label', 'line', 'Postcode']],
  'xilr.employerAddress': [['xilr.employerAddress.label', 'Label', 'line', 'Address']],
  'xilr.employerCity': [['xilr.employerCity.label', 'Label', 'line', 'City']],
  'xilr.lineManagerName': [['xilr.lineManagerName.label', 'Label', 'line', 'Line Manager name']],
  'xilr.lineManagerEmail': [['xilr.lineManagerEmail.label', 'Label', 'line', 'Line Manager email']],
  'xilr.lineManagerPhone': [['xilr.lineManagerPhone.label', 'Label', 'line', 'Line Manager phone']],
  'xilr.h.otherTraining': [['xilr.h.otherTraining.title', 'Section heading', 'line', 'Other training']],
  'xilr.otherTraining': [
    ['xilr.otherTraining.label', 'Question', 'line', 'Have you attended any other government funded training programmes in the last 12 months?'],
    ['xilr.otherTraining.when', 'Date label', 'line', 'When was it completed?'],
  ],
  'xilr.h.circumstances': [['xilr.h.circumstances.title', 'Section heading', 'line', 'Personal Circumstances']],
  'xilr.caring': [['xilr.caring.label', 'Question', 'line', 'Do you have any caring responsibilities?']],
  'xilr.otherCircumstances': [['xilr.otherCircumstances.label', 'Question', 'line', 'Are there any other personal circumstances you want to tell us about?']],
  'xilr.careLeaver': [['xilr.careLeaver.label', 'Question', 'line', 'Care leaver']],
  'xilr.h.understanding': [['xilr.h.understanding.title', 'Section heading', 'line', 'Programme understanding']],
  'xilr.programmeUnderstanding': [['xilr.programmeUnderstanding.label', 'Question', 'line', 'What is your understanding of the programme you are applying for?']],
  'xilr.careerProgression': [['xilr.careerProgression.label', 'Question', 'line', 'How will this programme help you in your career development/aspirations, and/or with your progression?']],
  'xilr.h.additional': [['xilr.h.additional.title', 'Section heading', 'line', 'Additional Information']],
  'xilr.jobRoleRelevance': [['xilr.jobRoleRelevance.label', 'Question', 'paragraph', IlrText.jobRoleRelevance]],
  'xilr.residenceNotFte': [['xilr.residenceNotFte.label', 'Question', 'paragraph', IlrText.residenceNotForFullTimeEducation]],
  'xilr.ehcp': [
    ['xilr.ehcp.heading', 'Heading', 'line', 'EHCP Status'],
    ['xilr.ehcp.sharing', 'Note before the question', 'paragraph', IlrText.ehcpSharing],
    ['xilr.ehcp.label', 'Question', 'paragraph', IlrText.ehcp],
    ['xilr.ehcp.use', 'Note after the question', 'paragraph', IlrText.ehcpUse],
  ],
  'xilr.over50Percent': [['xilr.over50Percent.label', 'Question', 'paragraph', IlrText.over50PercentEngland]],
  'xilr.wageRate': [
    ['xilr.wageRate.label', 'Question', 'paragraph', IlrText.wageRateBand],
    ['xilr.wageRate.linkIntro', 'Text before the rates link', 'line', 'If you are not sure use this link to check:'],
  ],
  'xilr.knownByOtherName': [
    ['xilr.knownByOtherName.label', 'Question', 'line', IlrText.knownByOtherName],
    ['xilr.knownByOtherName.otherNames', 'Follow-up label', 'line', IlrText.otherNames],
  ],
  'xilr.plrAccessAware': [
    ['xilr.plrAccessAware.record', 'PLR explanation', 'paragraph', IlrText.plrRecord],
    ['xilr.plrAccessAware.sharing', 'PLR sharing note', 'paragraph', IlrText.plrSharing],
    ['xilr.plrAccessAware.label', 'Question', 'paragraph', IlrText.plrAccessAware],
  ],
  'xilr.documentActions': [['xilr.documentActions.note', 'Note under the buttons', 'paragraph', 'The document contains every answer above plus the signature blocks, ready to print and sign. Filing it stores a copy against this learner’s Compliance documents.']],

  // ── CV/Job Description ──
  'cv.h.experience': [
    ['cv.h.experience.title', 'Section heading', 'line', 'Your Work Experience'],
    ['cv.h.experience.intro', 'Section intro', 'paragraph', CvJobText.intro],
  ],
  'cv.cvUpload': [['cv.cvUpload.label', 'Label', 'line', CvJobText.cvUpload]],
  'cv.experience': [['cv.experience.label', 'Question', 'paragraph', CvJobText.experience]],
  'cv.highestQualification': [['cv.highestQualification.label', 'Question', 'line', CvJobText.highestQualification]],
  'cv.highestQualificationField': [['cv.highestQualificationField.label', 'Question', 'line', CvJobText.highestQualificationField]],
  'cv.fieldQualification': [
    ['cv.fieldQualification.label', 'Question', 'line', 'Do you have any {field} qualifications?', FIELD_HINT],
    ['cv.fieldQualification.named', 'Follow-up label', 'line', 'Please specify your highest qualification in the field of {field}', FIELD_HINT],
    ['cv.fieldQualification.transcript', 'Transcript upload label', 'line', CvJobText.transcript],
  ],
  'cv.h.gcse': [
    ['cv.h.gcse.title', 'Section heading', 'line', 'GCSEs'],
    ['cv.h.gcse.note', 'Section note', 'paragraph', CvJobText.gcseNote],
  ],
  'cv.gcseEnglish': [
    ['cv.gcseEnglish.label', 'Question', 'line', CvJobText.gcseEnglish],
    ['cv.gcseEnglish.upload', 'Upload label', 'line', CvJobText.gcseEnglishUpload],
  ],
  'cv.gcseMaths': [
    ['cv.gcseMaths.label', 'Question', 'line', CvJobText.gcseMaths],
    ['cv.gcseMaths.upload', 'Upload label', 'line', CvJobText.gcseMathsUpload],
  ],
  'cv.h.functionalSkills': [
    ['cv.h.functionalSkills.title', 'Section heading', 'line', 'Functional Skills'],
    ['cv.h.functionalSkills.note', 'Section note', 'paragraph', CvJobText.functionalSkillsNote],
  ],
  'cv.functionalSkills': [['cv.functionalSkills.label', 'Question', 'paragraph', CvJobText.functionalSkills]],
};

export const TEXT_SLOTS_BY_ITEM: Record<string, TextSlot[]> = Object.fromEntries(
  Object.entries(SLOTS).map(([item, defs]) => [
    item,
    defs.map(([key, label, kind, def, hint]) => ({ key, item, label, kind, default: def, hint })),
  ])
);

export const TEXT_SLOTS: Record<string, TextSlot> = Object.fromEntries(
  Object.values(TEXT_SLOTS_BY_ITEM).flat().map((s) => [s.key, s])
);

/** Longest wording the builder accepts for a slot (the server enforces the same). */
export const MAX_TEXT_LENGTH = 20000;

/** The wording for `key`: the published edit, else the standard wording. */
export function textFor(texts: Record<string, string> | undefined, key: string): string {
  const edited = texts?.[key];
  if (typeof edited === 'string' && edited.trim() !== '') return edited;
  return TEXT_SLOTS[key]?.default ?? '';
}

/** Only edits for known slots, and only where they differ from the standard wording. */
export function cleanTexts(raw: unknown): Record<string, string> | undefined {
  if (!raw || typeof raw !== 'object') return undefined;
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
    const slot = TEXT_SLOTS[key];
    if (!slot || typeof value !== 'string' || value.trim() === '' || value === slot.default) continue;
    out[key] = value;
  }
  return Object.keys(out).length ? out : undefined;
}
