import { lazy } from 'react';
import { type RouteObject } from 'react-router-dom';
import { AccessGate, Home, LearnerStart } from './enrollmentGuards';

const Login = lazy(() => import('@/pages/login/page'));
const ForgotPassword = lazy(() => import('@/pages/forgot-password/page'));
const SetPassword = lazy(() => import('@/pages/set-password/page'));
const Application = lazy(() => import('@/pages/apprenticeship-application/page'));
const Users = lazy(() => import('@/pages/users/page'));
const Board = lazy(() => import('@/pages/users/BoardPage'));
const Wizard = lazy(() => import('@/pages/users/wizard/WizardPage'));
const WizardBuilder = lazy(() => import('@/pages/users/wizard-builder/page'));
const EligibilityBuilder = lazy(() => import('@/pages/users/eligibility-builder/page'));
const FirstSessions = lazy(() => import('@/pages/users/first-sessions/page'));
const CoachDirectory = lazy(() => import('@/pages/admin/coach-directory/page'));
const CoachBooking = lazy(() => import('@/pages/coach-booking/page'));
const Introduction = lazy(() => import('@/pages/lms-introduction/page'));
const Welcome = lazy(() => import('@/pages/learner/welcome/page'));
const Onboarding = lazy(() => import('@/pages/learner/onboarding/page'));
const Reviews = lazy(() => import('@/pages/learner/onboarding/reviews/page'));
const ReviewForm = lazy(() => import('@/pages/learner/onboarding/reviews/form'));
const Documents = lazy(() => import('@/pages/learner/compliance/page'));
const FirstSession = lazy(() => import('@/pages/learner/first-session/page'));
const EmployerDocuments = lazy(() => import('@/pages/employer/EmployerDocumentsPage'));
const ContinueInLms = lazy(() => import('@/pages/continue-lms/page'));

export const enrollmentRoutes: RouteObject[] = [
  { path: '/login', element: <Login /> },
  { path: '/forgot-password', element: <ForgotPassword /> },
  { path: '/set-password', element: <SetPassword mode="invitation" /> },
  { path: '/reset-password', element: <SetPassword mode="reset" /> },
  { path: '/apprenticeship_application', element: <Application /> },
  { path: '/coach-booking/:slug', element: <CoachBooking /> },
  { path: '/lms-introduction', element: <Introduction /> },
  { element: <AccessGate />, children: [
    ...['/', '/enrollment', '/choose-workspace'].map(path => ({ path, element: <Home /> })),
    { path: '/users', element: <Users /> },
    { path: '/users/wizard-builder', element: <WizardBuilder /> },
    { path: '/users/eligibility-builder', element: <EligibilityBuilder /> },
    { path: '/users/first-sessions', element: <FirstSessions /> },
    { path: '/users/coach-directory', element: <CoachDirectory workspaceRole="compliance" /> },
    { path: '/users/:userId', element: <Board /> },
    { path: '/users/:userId/wizard', element: <Wizard /> },
    { path: '/users/:userId/wizard/:stepSlug', element: <Wizard /> },
    { path: '/learner/welcome', element: <Welcome /> },
    { path: '/learner/onboarding', element: <Onboarding /> },
    { path: '/learner/onboarding/reviews', element: <Reviews /> },
    { path: '/learner/onboarding/reviews/:eventKey', element: <ReviewForm /> },
    { path: '/learner/onboarding/:stepSlug', element: <Onboarding /> },
    { path: '/learner/compliance-documents', element: <Documents /> },
    { path: '/learner/first-session', element: <FirstSession /> },
    ...['/learner', '/learner/home', '/workspace/learner', '/workspace/learner/dashboard'].map(path => ({ path, element: <LearnerStart /> })),
    { path: '/employers/:employerId/documents', element: <EmployerDocuments /> },
    { path: '/lms', element: <ContinueInLms /> },
    { path: '*', element: <ContinueInLms /> },
  ] },
];
