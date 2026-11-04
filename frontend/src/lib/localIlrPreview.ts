/** Temporary local inspection only. Never relaxes completion or submission. */
export function canPreviewIlrStep(slug: string | undefined): boolean {
  return import.meta.env.DEV
    && import.meta.env.MODE === 'eligibility-local'
    && import.meta.env.VITE_LOCAL_ILR_NAVIGATION_PREVIEW === '1'
    && ['localhost', '127.0.0.1', '[::1]'].includes(window.location.hostname)
    && (slug === 'ilr-details' || slug === 'ilr');
}
