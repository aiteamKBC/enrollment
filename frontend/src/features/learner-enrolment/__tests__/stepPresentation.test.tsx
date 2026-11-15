import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { LearnerEnrolmentVisualContext } from '../enrolmentPresentation';
import { LearnerStepContent } from '../LearnerStepContent';
import { informationSections } from '../stepPresentation';
import { parseRich } from '@/pages/users/wizard/layout/richFormat';
import { RichText } from '@/pages/users/wizard/layout/RichText';
import { WizardTextsContext } from '@/pages/users/wizard/layout/textsContext';
import { textFor } from '@/pages/users/wizard/layout/texts';
import BeforeYouBegin from '@/pages/users/wizard/steps/BeforeYouBegin';
import SkillsRadar from '@/pages/users/wizard/steps/SkillsRadar';
import { WizardContext, type WizardContextValue } from '@/pages/users/wizard/WizardContext.helpers';
import type { WizardDraft } from '@/pages/users/types';

const ksbs = [{ id: 'k1', codes: ['K1'], title: 'Existing knowledge wording', theme: 'Knowledge', kind: 'Knowledge' },
  { id: 's1', codes: ['S1'], title: 'Existing skill wording', theme: 'Skills', kind: 'Skill' }];
vi.mock('@/api/curriculum', () => ({ peekKsbProfile: () => ({ results: ksbs }), fetchKsbProfile: vi.fn(async () => ({ results: ksbs })) }));
beforeEach(() => vi.stubGlobal('AppIcon', () => <i />));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function wrap(slug: string, child: React.ReactNode, learner = true) {
  return <LearnerEnrolmentVisualContext.Provider value={learner}><LearnerStepContent slug={slug}>{child}</LearnerStepContent></LearnerEnrolmentVisualContext.Provider>;
}

it.each(['before-you-begin', 'ilr-details', 'ilr', 'programme-suitability', 'plr', 'inclusion', 'cv-job', 'skills-radar'])('scopes the %s content styling to the learner without changing its controls', slug => {
  const click = vi.fn();
  const { rerender, container } = render(wrap(slug, <button onClick={click}>Existing action</button>, false));
  expect(container.querySelector('[data-enrolment-step]')).toBeNull();
  rerender(wrap(slug, <button onClick={click}>Existing action</button>));
  expect(container.querySelector('[data-enrolment-step]')).toHaveAttribute('data-enrolment-step', slug);
  screen.getByRole('button', { name: 'Existing action' }).click();
  expect(click).toHaveBeenCalledOnce();
});

it.each(['introduction', 'policies', 'initial-assessments', 'next-steps', 'custom-team'])('leaves the unrequested %s content unchanged', slug => {
  const { container } = render(wrap(slug, <p>Original content</p>));
  expect(container.innerHTML).toBe('<p>Original content</p>');
});

it.each([textFor(undefined, 'block.beforeYouBegin.body'), '## Published heading\nCustom wording.\n\n## Documents\n- First\n- Second\n\nOriginal conclusion.', 'Only the original paragraph.'])('retains every rich block and word from the original published Before You Begin text: %#', source => {
  expect(informationSections(source).flat()).toEqual(parseRich(source));
  const original = render(<RichText source={source} />), text = original.container.textContent;
  original.unmount();
  const { container } = render(<WizardTextsContext.Provider value={{ 'block.beforeYouBegin.title': 'Existing heading', 'block.beforeYouBegin.body': source }}>
    {wrap('before-you-begin', <BeforeYouBegin />)}
  </WizardTextsContext.Provider>);
  container.querySelector('h2')!.remove();
  expect(container.textContent).toBe(text);
  expect(screen.queryByRole('button')).toBeNull();
});

function radar(staff = false) {
  const setSection = vi.fn();
  const draft = { skillsRadar: { assessments: { k1: { ksbId: 'k1', level: 'never', note: 'Stored note', evidenceFiles: ['stored.pdf'] }, s1: { ksbId: 's1', level: null } } } } as unknown as WizardDraft;
  const value = { draft, setSection, readOnly: staff, board: { programme: { name: 'Existing Programme' } } } as unknown as WizardContextValue;
  return { view: <WizardContext.Provider value={value}>{wrap('skills-radar', <SkillsRadar />, !staff)}</WizardContext.Provider>, draft, setSection };
}

it('shows Assess for the seeded unanswered row and Edit for the saved lowest rating, without changing counts or answers', async () => {
  const { view, draft, setSection } = radar();
  const before = structuredClone(draft); render(view);
  expect(screen.getByText('1 of 2 answered')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Assess' })).toBeVisible();
  await userEvent.click(screen.getByRole('button', { name: 'Edit' }));
  expect(screen.getByDisplayValue('Stored note')).toBeVisible();
  expect(screen.getByRole('radio', { name: /^Never/ })).toBeChecked();
  expect(screen.getByText('stored.pdf')).toBeVisible();
  expect(draft).toEqual(before);
  expect(setSection).not.toHaveBeenCalled();
});

it('keeps staff Skills Radar read-only and outside the new content presentation', () => {
  const { view } = radar(true); const { container } = render(view);
  expect(container.querySelector('[data-enrolment-step]')).toBeNull();
  expect(screen.getAllByRole('button', { name: 'View' })).toHaveLength(2);
  expect(screen.queryByRole('button', { name: 'Assess' })).toBeNull();
});
