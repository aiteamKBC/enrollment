import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { AuthContext } from '@/hooks/useAuth.helpers';
import { WorkspaceShell } from '@/components/feature/EnrollmentShell';
import Introduction from '@/pages/users/wizard/steps/Introduction';
import { WizardTextsContext } from '@/pages/users/wizard/layout/textsContext';
import { textFor } from '@/pages/users/wizard/layout/texts';
import { parseRich } from '@/pages/users/wizard/layout/richFormat';
import { RichText } from '@/pages/users/wizard/layout/RichText';
import { EnrolmentHero, WelcomeContent } from '../EnrolmentVisuals';
import { LearnerEnrolmentVisualContext, welcomeSections } from '../enrolmentPresentation';

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const source = textFor(undefined, 'block.introduction.body');
const normalized = (value: string | null) => value?.replace(/\s+/g, ' ').trim();

describe('Welcome content and official assets', () => {
  it.each([source, 'An unchanged paragraph.', '## Custom title\nParagraph with **emphasis** and a [link](https://example.test).\n\n- One\n- Two\n\nLast paragraph.', '**Custom list title**\n- First\n- Second\n\nFinal words.'])('retains every parsed block in its original order: %#', body => {
    const grouped = welcomeSections(body);
    expect([...grouped.lead, ...grouped.cards.flat(), ...grouped.conclusion]).toEqual(parseRich(body));
    const original = render(<RichText source={body} />), before = normalized(original.container.textContent); original.unmount();
    const themed = render(<WelcomeContent title="Original title" source={body} />);
    expect(screen.getByRole('heading', { name: 'Original title' })).toBeInTheDocument();
    themed.container.querySelector('h2')!.remove();
    expect(normalized(themed.container.textContent)).toBe(before);
  });
  it('uses the same Builder wording for the learner presentation and leaves the staff view unchanged', () => {
    const texts = { 'block.introduction.title': 'Our published welcome', 'block.introduction.body': source + '\n\nCUSTOM-PUBLISHED-PARAGRAPH' };
    const { rerender, container } = render(<WizardTextsContext.Provider value={texts}><Introduction /></WizardTextsContext.Provider>);
    const original = normalized(container.textContent);
    expect(container.querySelector('[class*="welcomeCards"]')).toBeNull();
    rerender(<WizardTextsContext.Provider value={texts}><LearnerEnrolmentVisualContext.Provider value><Introduction /></LearnerEnrolmentVisualContext.Provider></WizardTextsContext.Provider>);
    expect(normalized(container.textContent)).toBe(original);
    expect(screen.getByRole('heading', { name: 'Our published welcome' })).toBeInTheDocument();
    expect(container.querySelectorAll('[class*="welcomeCard_"]')).toHaveLength(3);
    expect(container.textContent?.match(/CUSTOM-PUBLISHED-PARAGRAPH/g)).toHaveLength(1);
  });
  it('keeps safe links and full lists accessible instead of turning cards into fake controls', () => {
    render(<WelcomeContent title="Welcome" source={'## Content\nText\n\n## Details\nRead [the guide](https://example.test/guide).\n\n- First original bullet\n- Second original bullet'} />);
    expect(screen.getByRole('link', { name: 'the guide' })).toHaveAttribute('href', 'https://example.test/guide');
    expect(screen.getAllByRole('listitem')).toHaveLength(2);
    expect(screen.queryByRole('button')).toBeNull();
  });
  it('uses the supplied building bytes and original unmodified KBC logo', () => {
    const bytes = (name: string) => readFileSync('public/' + name);
    expect(createHash('sha256').update(bytes('assets/document-branding/kbc-logo.png')).digest('hex')).toBe('9e1b69be5626bf13e324bb46922e8db167e50c2e4aee68446274b302b4ff1111');
    const hash = (path: string) => createHash('sha256').update(bytes(path)).digest('hex');
    expect(hash('images/kbc-enrolment-hero.png')).toBe(hash('images/kbc-enrolment-hero.png.png'));
    render(<EnrolmentHero />);
    expect(screen.getByRole('img', { name: 'Kent Business College building' })).toHaveAttribute('src', '/images/kbc-enrolment-hero.png');
    expect(screen.getByRole('heading', { name: 'Your enrolment' })).toBeInTheDocument();
  });
});

function openShell(role: string, path = '/learner/onboarding/introduction', commercial = false) {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ count: 0, results: [] }), { headers: { 'Content-Type': 'application/json' } })));
  const logout = vi.fn();
  const account = { role, id: 'scope-test', subjectId: '42', displayName: 'Actual authenticated learner', access: 'enrolment', learnerType: commercial ? 'commercial' : 'apprenticeship' };
  render(<MemoryRouter initialEntries={[path]}><AuthContext.Provider value={{ auth: { account }, logout } as any}><WorkspaceShell pageTitle="My Enrolment" pageSubtitle="Complete your onboarding" role={role} roleLabel={role} navItems={[]}>Existing content</WorkspaceShell></AuthContext.Provider></MemoryRouter>);
  return logout;
}
describe('strict learner-side scope', () => {
  it.each(['staff', 'admin', 'employer'])('retains the original %s shell without the new learner branding', role => {
    openShell(role, role === 'employer' ? '/employers/42/documents' : '/users');
    expect(screen.queryByRole('img', { name: 'Kent Business College' })).toBeNull();
    expect(document.querySelector('[class*="learnerEnrolment"]')).toBeNull();
    if (role !== 'employer') expect(screen.getByRole('link', { name: 'Eligibility applications' })).toHaveAttribute('href', '/users/eligibility-applications');
  });
  it('preserves commercial learner navigation outside apprenticeship enrolment', () => {
    openShell('learner', '/learner/first-session', true);
    expect(screen.queryByRole('img', { name: 'Kent Business College' })).toBeNull();
    expect(screen.queryByRole('link', { name: 'My enrolment' })).toBeNull();
  });
  it('shows the original logo, configured identity and correct nested-step active state only for the learner', () => {
    openShell('learner');
    expect(screen.getByRole('img', { name: 'Kent Business College' })).toHaveAttribute('src', '/assets/document-branding/kbc-logo.png');
    expect(screen.getByRole('link', { name: 'My enrolment' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByText('Actual authenticated learner')).toBeInTheDocument();
    expect(screen.getByText('Complete your onboarding')).toBeInTheDocument();
  });
  it('highlights Reviews alone when a learner opens a nested review', () => {
    openShell('learner', '/learner/onboarding/reviews/existing-review');
    expect(screen.getByRole('link', { name: 'Reviews' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('link', { name: 'My enrolment' })).not.toHaveAttribute('aria-current');
  });
  it('keeps account settings and sign-out actions functional', () => {
    const logout = openShell('learner');
    fireEvent.click(screen.getByRole('button', { name: 'Open account menu for Actual authenticated learner' }));
    expect(screen.getByRole('menuitem', { name: 'Account Settings' })).toHaveAttribute('href', '/learner/account-settings');
    fireEvent.click(screen.getByRole('menuitem', { name: 'Sign Out' })); expect(logout).toHaveBeenCalledOnce();
  });
});
