import { createElement, Suspense } from 'react';
import { useRoutes } from 'react-router-dom';
import { enrollmentRoutes } from './enrollmentRoutes';

export function AppRoutes() {
  const element = useRoutes(enrollmentRoutes);
  return createElement(Suspense, { fallback: createElement('p', { role: 'status', className: 'p-6' }, 'Loading enrolment…') }, element);
}
