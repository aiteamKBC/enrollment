/** Temporary local design access. Does not change validation or completion. */
export function canPreviewFinalEnrolmentStep(slug: string | undefined): boolean {
  return import.meta.env.DEV
    && import.meta.env.VITE_LOCAL_ENROLMENT_DESIGN_PREVIEW === '1'
    && typeof window !== 'undefined'
    && ['localhost', '127.0.0.1', '[::1]'].includes(window.location.hostname)
    && ['policies', 'initial-assessments', 'next-steps'].includes(slug || '');
}
