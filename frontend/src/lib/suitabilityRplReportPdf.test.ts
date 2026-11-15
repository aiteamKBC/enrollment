import { describe, expect, it } from 'vitest';
import {
  buildSuitabilityRplReportPdf,
  formatSuitabilityRplDate,
  PROGRAMME_UNDERSTANDING_QUESTIONS,
  SUITABILITY_RPL_SECTION_TITLES,
} from './suitabilityRplReportPdf';

describe('Suitability & RPL blank report PDF', () => {
  it('preserves the two Programme Understanding questions used by the enrolment wizard', () => {
    expect(PROGRAMME_UNDERSTANDING_QUESTIONS).toEqual([
      'What is your understanding of the programme you are applying for?',
      'How will this programme help you in your career development/aspirations, and/or with your progression?',
    ]);
  });

  it('contains the six approved report sections after removing evidence and final assessment sections', () => {
    expect(SUITABILITY_RPL_SECTION_TITLES).toEqual([
      'Learner & Programme Details',
      'Programme Understanding',
      'Programme Suitability Assessment',
      'Skills Radar Assessment',
      'Recognition of Prior Learning Assessment',
      'Declarations & Signatures',
    ]);
  });

  it('formats assessment dates in readable UK form', () => {
    expect(formatSuitabilityRplDate('2026-11-05T12:00:00Z')).toBe('05 November 2026');
    expect(formatSuitabilityRplDate('not-a-date')).toBe('not-a-date');
  });

  it('builds a valid four-page A4 portrait document without learner data', () => {
    const document = buildSuitabilityRplReportPdf();
    expect(document.getNumberOfPages()).toBe(4);
    for (let page = 1; page <= 4; page += 1) {
      document.setPage(page);
      expect(document.internal.pageSize.getWidth()).toBeCloseTo(210, 1);
      expect(document.internal.pageSize.getHeight()).toBeCloseTo(297, 1);
    }
    const bytes = new Uint8Array(document.output('arraybuffer'));
    expect(new TextDecoder().decode(bytes.slice(0, 5))).toBe('%PDF-');
    const commands = (document.internal.pages as unknown as string[][]).flat().join('\n');
    expect(commands).toContain('CATEGORY');
    expect(commands).toContain('AVERAGE / 9');
    expect((commands.match(/N\/A/g) || []).length).toBe(3);
    expect(commands).not.toContain('Final Assessment & Recommendation');
    expect(commands).not.toContain('SUITABILITY OUTCOME');
    expect(commands).not.toContain('ASSESSMENT RATIONALE');
    expect(commands).not.toContain('FINAL TRAINING RECOMMENDATION');
    expect(commands).not.toContain('DOCUMENT CONTROL');
    expect(commands).not.toContain('CV, Qualifications & PLR Evidence');
    expect(commands).not.toContain('Curriculum Vitae (CV)');
    expect(commands).not.toContain('Personal Learning Record (PLR)');
    expect(commands).not.toContain('CV, QUALIFICATIONS AND PLR EVIDENCE');
    expect(commands).toContain('Declarations & Signatures');
  });

  it('omits final assessment content even when legacy values are supplied', () => {
    const document = buildSuitabilityRplReportPdf({
      status: 'finalised',
      finalAssessment: {
        outcome: 'suitable',
        rationale: 'Legacy final rationale must remain internal to the report record.',
        reportReference: 'LEGACY-REF',
        version: '7',
        approvalDate: '05 November 2026',
      },
    });
    const commands = (document.internal.pages as unknown as string[][]).flat().join('\n');
    expect(commands).not.toContain('Legacy final rationale must remain internal to the report record.');
    expect(commands).not.toContain('SUITABILITY OUTCOME');
    expect(commands).not.toContain('ASSESSMENT RATIONALE');
    expect(commands).not.toContain('FINAL TRAINING RECOMMENDATION');
    expect(commands).not.toContain('DOCUMENT CONTROL');
    expect(commands).not.toContain('LEGACY-REF');
  });

  it('renders the saved full-programme decision without an empty reduction table', () => {
    const document = buildSuitabilityRplReportPdf({
      status: 'draft',
      rpl: {
        relevantPriorLearning: false,
        reductionJustified: false,
        originalHours: 368.25,
        justification: 'The supplied evidence does not substantiate an exemption from the standard Training Plan.',
        reviewed: true,
        rows: [],
      },
    });
    const commands = (document.internal.pages as unknown as string[][]).flat().join('\n');
    expect(commands).toContain('No Relevant Prior Learning - Full Standard Training Plan');
    expect(commands).toContain('Original planned hours: 368.25 hours');
    expect(commands).toContain('No training reduction approved.');
    expect(commands).toContain('RPL ASSESSMENT JUSTIFICATION');
    expect(commands).not.toContain('MODULE-LEVEL REDUCTIONS');
    expect(commands).not.toContain('KSB LINK');
    expect(commands).not.toContain('VERIFIED EVIDENCE');
  });

  it('preserves historical no-reduction decisions without reinterpreting them', () => {
    const document = buildSuitabilityRplReportPdf({
      status: 'finalised',
      rpl: {
        relevantPriorLearning: true,
        reductionJustified: false,
        originalHours: 120,
        evidenceReview: 'Historical evidence review must remain internal.',
        noReductionReason: 'Historical reason must remain internal.',
        justification: 'Historical approved RPL justification.',
        reviewed: true,
      },
    });
    const commands = (document.internal.pages as unknown as string[][]).flat().join('\n');
    expect(commands).toContain('Relevant Prior Learning - No Training Reduction Approved');
    expect(commands).toContain('Historical decision retained with no training reduction approved.');
    expect(commands).toContain('Historical approved RPL justification.');
    expect(commands).not.toContain('Historical evidence review must remain internal.');
    expect(commands).not.toContain('Historical reason must remain internal.');
    expect((commands.match(/Historical approved RPL justification\./g) || []).length).toBe(1);
  });

  it('renders populated no-reduction and multi-module reduction scenarios without clipping rows', () => {
    const base = {
      status: 'draft' as const,
      learner: { fullName: 'Synthetic Learner', programme: 'Synthetic Programme', jobTitle: 'Project Officer' },
      programmeUnderstanding: [
        { question: PROGRAMME_UNDERSTANDING_QUESTIONS[0], answer: 'A concise saved answer.' },
        { question: PROGRAMME_UNDERSTANDING_QUESTIONS[1], answer: 'A second saved answer.' },
      ],
      skillsRadar: Array.from({ length: 18 }, (_, index) => ({ label: `K${index + 1} — Synthetic saved score`, score: (index % 9) + 1 })),
      skillsRadarSummary: [
        { label: 'Knowledge', score: 5.5, answered: 12, total: 12 },
        { label: 'Skills', score: 6.2, answered: 4, total: 4 },
        { label: 'Behaviours', score: 4.8, answered: 2, total: 2 },
      ],
      evidence: { cv: 'Provided — synthetic experience', qualifications: 'Not provided', plr: 'Not available - integration not implemented' },
      programmeSuitability: {
        pathway: 'marketing',
        responses: Array.from({ length: 9 }, (_, index) => ({
          group: `Marketing group ${index + 1}`,
          question: `Which responsibilities apply to group ${index + 1}?`,
          answers: [`Selected option ${index + 1}A`, `Selected option ${index + 1}B`],
        })),
        justification: 'Synthetic approved programme suitability justification.',
        reviewed: true,
      },
    };
    const noReduction = buildSuitabilityRplReportPdf({ ...base, rpl: {
      relevantPriorLearning: true, reductionJustified: false,
      evidenceReview: 'Verified evidence was reviewed.', noReductionReason: 'The full programme remains appropriate.',
      justification: 'Synthetic reviewed RPL justification.', reviewed: true,
    } });
    expect(noReduction.getNumberOfPages()).toBeGreaterThanOrEqual(4);
    const noReductionCommands = (noReduction.internal.pages as unknown as string[][]).flat().join('\n');
    expect(noReductionCommands).toContain('Synthetic reviewed RPL justification.');
    expect(noReductionCommands).toContain('Relevant Prior Learning - No Training Reduction Approved');
    expect(noReductionCommands).not.toContain('MODULE-LEVEL REDUCTIONS');
    expect(noReductionCommands).not.toContain('ASSESSOR-REVIEWED RPL JUSTIFICATION');
    expect(noReductionCommands).toContain('EMPLOYER / LINE MANAGER');
    expect(noReductionCommands).not.toContain('Curriculum Vitae (CV)');
    expect(noReductionCommands).not.toContain('Personal Learning Record (PLR)');
    expect(noReductionCommands).not.toContain('CV, QUALIFICATIONS AND PLR EVIDENCE');
    expect(noReductionCommands).toContain('Knowledge');
    expect(noReductionCommands).toContain('Skills');
    expect(noReductionCommands).toContain('Behaviours');
    expect(noReductionCommands).toContain('GROUP');
    expect(noReductionCommands).toContain('ANSWERS');
    expect(noReductionCommands).not.toContain('SKILL / COMPETENCY');
    for (let index = 1; index <= 9; index += 1) {
      expect(noReductionCommands).toContain(`Marketing group ${index}`);
      expect((noReductionCommands.match(new RegExp(`Marketing group ${index}`, 'g')) || []).length).toBe(1);
      expect(noReductionCommands).toContain(`Selected option ${index}A`);
      expect(noReductionCommands).toContain(`Selected option ${index}B`);
    }
    expect(noReductionCommands).toContain('PROGRAMME SUITABILITY JUSTIFICATION');
    expect(noReductionCommands).toContain('Synthetic approved programme suitability justification.');
    expect(noReductionCommands).not.toContain('Unselected option');
    expect(noReductionCommands).not.toContain('ORIGINAL SELECTED ANSWERS');
    expect(noReductionCommands).not.toContain('ORIGINAL QUESTION');
    expect(noReductionCommands).not.toContain('SELECTED  /');
    expect(noReductionCommands.lastIndexOf('PROGRAMME SUITABILITY JUSTIFICATION')).toBeGreaterThan(
      noReductionCommands.lastIndexOf('Selected option 9B'),
    );
    const unapproved = buildSuitabilityRplReportPdf({ ...base, programmeSuitability: {
      ...base.programmeSuitability,
      justification: 'Unapproved draft must stay out of the final PDF.',
      reviewed: false,
    } });
    const unapprovedCommands = (unapproved.internal.pages as unknown as string[][]).flat().join('\n');
    expect(unapprovedCommands).not.toContain('Unapproved draft must stay out of the final PDF.');

    const unapprovedRpl = buildSuitabilityRplReportPdf({ ...base, rpl: {
      relevantPriorLearning: false,
      reductionJustified: false,
      justification: 'Unapproved RPL draft must stay out of the final PDF.',
      reviewed: false,
    } });
    const unapprovedRplCommands = (unapprovedRpl.internal.pages as unknown as string[][]).flat().join('\n');
    expect(unapprovedRplCommands).not.toContain('Unapproved RPL draft must stay out of the final PDF.');

    const reduced = buildSuitabilityRplReportPdf({ ...base, rpl: {
      relevantPriorLearning: true, reductionJustified: true,
      evidenceReview: 'Verified evidence was reviewed.', justification: 'Synthetic reduction justification.', reviewed: true,
      rows: Array.from({ length: 12 }, (_, index) => ({
        area: `Authoritative module ${index + 1}`, ksb: `K${index + 1}`,
        plannedHours: (index + 1) * 10, reducedHours: index + 1,
        adjustedHours: (index + 1) * 9, evidence: 'Synthetic certificate and professional discussion.', hours: index + 1,
      })),
      originalHours: 500, reductionHours: 78, adjustedHours: 422,
    } });
    expect(reduced.getNumberOfPages()).toBeGreaterThan(noReduction.getNumberOfPages());
    const reducedCommands = (reduced.internal.pages as unknown as string[][]).flat().join('\n');
    expect(reducedCommands).toContain('Relevant Prior Learning - Training Reduction Approved');
    expect(reducedCommands).toContain('ORIGINAL HOURS');
    expect(reducedCommands).toContain('APPROVED REDUCTION');
    expect(reducedCommands).toContain('ADJUSTED HOURS');
    expect(reducedCommands).toContain('MAPPED KSBs');
    expect(reducedCommands).not.toContain('VERIFIED EVIDENCE');
    for (let page = 1; page <= reduced.getNumberOfPages(); page += 1) {
      reduced.setPage(page);
      expect(reduced.internal.pageSize.getHeight()).toBeCloseTo(297, 1);
    }

    const manyGroups = buildSuitabilityRplReportPdf({ ...base, programmeSuitability: {
      ...base.programmeSuitability,
      responses: Array.from({ length: 18 }, (_, index) => ({
        group: `Long group ${index + 1}`,
        question: `Original question ${index + 1}`,
        selectedOptions: Array.from({ length: 4 }, (_, optionIndex) => ({
          value: `${index + 1}-${optionIndex + 1}`,
          label: `A long saved selected answer for group ${index + 1}, option ${optionIndex + 1}, which should wrap cleanly across the repeated table pages.`,
        })),
      })),
    } });
    const manyGroupCommands = (manyGroups.internal.pages as unknown as string[][]).flat().join('\n');
    expect(manyGroups.getNumberOfPages()).toBeGreaterThan(noReduction.getNumberOfPages());
    expect((manyGroupCommands.match(/GROUP/g) || []).length).toBeGreaterThan(1);
    expect((manyGroupCommands.match(/ANSWERS/g) || []).length).toBeGreaterThan(1);
  });
});
