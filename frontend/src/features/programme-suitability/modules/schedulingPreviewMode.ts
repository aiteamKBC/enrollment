/** Local opt-in only. A production build cannot enable this via the URL. */
export function schedulingPreviewEnabled(): boolean {
  return import.meta.env.DEV && typeof window !== 'undefined'
    && ['localhost', '127.0.0.1', '[::1]'].includes(window.location.hostname)
    && new URLSearchParams(window.location.search).get('moduleSchedulePreview') === '1'
    && new URLSearchParams(window.location.search).get('moduleScheduleReview') !== '1';
}
