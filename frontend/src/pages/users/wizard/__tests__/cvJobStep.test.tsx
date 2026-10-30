/**
 * CV/Job Description — redesigned step: uploads go to storage, the "field"
 * questions follow the learner's programme, and follow-up fields appear only
 * when they apply.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { CvJobForm, WizardDraft } from '../../types';
import { WIZARD_STEPS } from '../../types';
import { missingForStep } from '../validation';
import { programmeField } from '../steps/cvJobText';

const api = vi.hoisted(() => ({
  fetchCvDocuments: vi.fn(),
  uploadCvDocument: vi.fn(),
  deleteCvDocument: vi.fn(),
  getCvDocumentUrl: vi.fn(),
}));
vi.mock('@/api/cvJobDocuments', () => api);
const wizard = vi.hoisted(() => ({ cvJob: {} as CvJobForm, programme: 'Marketing Executive Level 4', setSection: vi.fn() }));
vi.mock("../WizardContext.helpers", () => ({
  useWizard: () => ({
    userId: '41',
    isCommercial: false,
    board: { programme: { name: wizard.programme } },
    draft: { cvJob: wizard.cvJob },
    setSection: wizard.setSection,
  }),
}));
vi.mock("@/hooks/useToast.helpers", () => ({ useToast: () => ({ success: vi.fn(), error: vi.fn() }) }));

import CvJob from '../steps/CvJob';

const COMPLETE: CvJobForm = {
  pmQualifications: '', experienceText: 'N/A', functionalSkillsEnrol: 'No, I would prefer to opt out',
  highestQualification: 'BA (Hons)', highestQualificationField: 'Business', hasFieldQualification: false,
  highestFieldQualification: '', gcseEnglish: true, gcseMaths: false,
};
const CV_STEP = WIZARD_STEPS.findIndex((s) => s.slug === 'cv-job');
const missing = (cvJob: Partial<CvJobForm>) => missingForStep(CV_STEP, { cvJob: { ...COMPLETE, ...cvJob } } as WizardDraft);

beforeEach(() => {
  vi.clearAllMocks();
  wizard.cvJob = COMPLETE;
  wizard.programme = 'Marketing Executive Level 4';
  api.fetchCvDocuments.mockResolvedValue([]);
});

describe('CV/Job Description step', () => {
  it('uploads the CV to storage for this learner instead of keeping its name', async () => {
    const saved = { id: 'd1', docKind: 'cv', filename: 'cv.pdf', contentType: 'application/pdf', sizeBytes: 4, uploadedAt: null };
    api.uploadCvDocument.mockResolvedValue(saved);
    render(<CvJob />);
    await waitFor(() => expect(api.fetchCvDocuments).toHaveBeenCalledWith('apprenticeship', '41'));

    const file = new File(['%PDF'], 'cv.pdf', { type: 'application/pdf' });
    await userEvent.upload(screen.getByLabelText(/Please upload an up to date CV/), file);

    expect(api.uploadCvDocument).toHaveBeenCalledWith('apprenticeship', '41', 'cv', file);
    expect(await screen.findByRole('button', { name: 'cv.pdf' })).toBeInTheDocument();
    expect(wizard.setSection).not.toHaveBeenCalled();
  });

  it('words the field questions for the learner’s programme', () => {
    render(<CvJob />);
    expect(screen.getByRole('group', { name: 'Do you have any marketing qualifications?' })).toBeInTheDocument();

    expect(programmeField('Project Controls Professional Level 6')).toBe('project controls');
    expect(programmeField('Associate Project Manager Level 4')).toBe('project management');
    expect(programmeField('OTHM LEVEL 7 DIPLOMA IN PROJECT MANAGEMENT - Al Fanar')).toBe('project management');
    expect(programmeField('Marketing Manager Level 6')).toBe('marketing');
    expect(programmeField('')).toBeNull();
  });

  it('uses neutral wording for any other programme', () => {
    wizard.programme = 'Test LMS Curriculum';
    render(<CvJob />);
    expect(screen.getByRole('group', { name: 'Do you have any qualifications in the field of your programme?' })).toBeInTheDocument();
  });

  it('asks for the field qualification and transcript only on Yes, and clears the answer on No', async () => {
    render(<CvJob />);
    expect(screen.queryByText('Please specify your highest qualification in the field of marketing')).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/transcript of this degree/)).not.toBeInTheDocument();

    wizard.cvJob = { ...COMPLETE, hasFieldQualification: true, highestFieldQualification: 'CIM Level 4' };
    const { unmount } = render(<CvJob />);
    expect(screen.getByText('Please specify your highest qualification in the field of marketing')).toBeInTheDocument();
    expect(screen.getByLabelText(/transcript of this degree/)).toBeInTheDocument();

    const group = screen.getAllByRole('group', { name: 'Do you have any marketing qualifications?' }).at(-1)!;
    await userEvent.click(within(group).getByRole('radio', { name: 'No' }));
    expect(wizard.setSection).toHaveBeenLastCalledWith('cvJob', expect.objectContaining({
      hasFieldQualification: false, highestFieldQualification: '',
    }));
    unmount();
  });

  it('offers GCSE evidence uploads only for a GCSE the learner has', () => {
    render(<CvJob />);
    expect(screen.getByLabelText('Please upload your English GCSE evidence:')).toBeInTheDocument();
    expect(screen.queryByLabelText('Please upload your Maths GCSE evidence:')).not.toBeInTheDocument();
  });

  it('keeps an uploaded file visible even if the answer is changed to No', async () => {
    api.fetchCvDocuments.mockResolvedValue([
      { id: 'm1', docKind: 'gcse-maths', filename: 'maths.png', contentType: 'image/png', sizeBytes: 4, uploadedAt: null },
    ]);
    render(<CvJob />);
    expect(await screen.findByRole('button', { name: 'maths.png' })).toBeInTheDocument();
  });

  it('offers the five Functional Skills choices', () => {
    render(<CvJob />);
    const select = screen.getAllByRole('combobox').at(-1)!;
    expect(within(select).getAllByRole('option').map((o) => o.textContent)).toEqual([
      'Select…',
      'Yes, I would like to enrol in the funded English and Maths Functional Skills courses',
      'I would like to enrol in Maths functional skills course only',
      'I would like to enrol in English functional skills course only',
      'No, I would prefer to opt out',
      'I have uploaded my GCSE certificates',
    ]);
  });

  it('requires the starred answers, and the field qualification only on Yes', () => {
    expect(missing({})).toEqual([]);
    expect(missing({
      experienceText: '', highestQualification: '', highestQualificationField: '', hasFieldQualification: null,
      gcseEnglish: null, gcseMaths: null, functionalSkillsEnrol: '',
    })).toEqual([
      'Previous experience (or N/A)', 'Highest-level qualification', 'Field of highest qualification',
      'Qualifications in your programme field', 'GCSE in English', 'GCSE in Maths', 'Functional Skills enrolment',
    ]);
    expect(missing({ hasFieldQualification: true, highestFieldQualification: '' })).toEqual(['Highest qualification in your programme field']);
  });

  it('no longer asks the project management question', () => {
    render(<CvJob />);
    expect(screen.queryByText(/project management qualifications\? If yes/)).not.toBeInTheDocument();
    expect(missing({ pmQualifications: '' })).toEqual([]);
  });
});
