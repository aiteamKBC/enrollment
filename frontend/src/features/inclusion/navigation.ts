const DEFAULT_INCLUSION_ORIGIN = 'https://admin.kentbusinesscollege.net';

/** External Inclusion owns tickets; the LMS owns only this navigation entry. */
export function inclusionLoginUrl(origin = import.meta.env.VITE_INCLUSION_URL || DEFAULT_INCLUSION_ORIGIN) {
  return `${origin.replace(/\/$/, '')}/login`;
}
