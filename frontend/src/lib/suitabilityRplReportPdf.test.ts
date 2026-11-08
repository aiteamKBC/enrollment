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

  it('contains all eight approved report sections', () => {
    expect(SUITABILITY_RPL_SECTION_TITLES).toEqual([
      'Learner & Programme Details',
      'Programme Understanding',
      'Programme Suitability Assessment',
      'Skills Radar Assessment',
      'CV, Qualifications & PLR Evidence',
      'Recognition of Prior Learning Assessment',
      'Final Assessment & Recommendation',
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
    expect(noReductionCommands).not.toContain('ASSESSOR-REVIEWED RPL JUSTIFICATION');
    expect(noReductionCommands).toContain('EMPLOYER / LINE MANAGER');
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

    const reduced = buildSuitabilityRplReportPdf({ ...base, rpl: {
      relevantPriorLearning: true, reductionJustified: true,
      evidenceReview: 'Verified evidence was reviewed.', justification: 'Synthetic reduction justification.', reviewed: true,
      rows: Array.from({ length: 12 }, (_, index) => ({
        area: `Authoritative module ${index + 1}`, ksb: `K${index + 1}`,
        evidence: 'Synthetic certificate and professional discussion.', hours: index + 1,
      })),
      originalHours: 500, reductionHours: 78, adjustedHours: 422,
    } });
    expect(reduced.getNumberOfPages()).toBeGreaterThan(noReduction.getNumberOfPages());
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
