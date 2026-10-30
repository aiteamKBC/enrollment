


/**
 * Format rules for typed inputs.
 *
 * `type="email"` / `type="tel"` alone do not enforce anything here: the browser
 * only applies its own email check on native form submit, and never validates
 * `tel` at all. These fields live outside a <form>, so without an explicit check
 * a single letter is accepted as an email address or a phone number.
 */
// Deliberately permissive: something@something.tld, no spaces. Stricter regexes
// reject addresses that are legitimately valid.
export const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;

// UK-friendly: digits with optional +, spaces, dashes, brackets; 10-15 digits.
export const PHONE_RE = /^\+?[\d\s().-]{9,}$/;

export const digitsOf = (v: string) => v.replace(/\D/g, '');


/** A format complaint for a filled value, or '' when it is acceptable. */
export function formatError(type: string, value: string): string {
  const v = value.trim();
  if (!v) return ''; // emptiness is the required-check's job, not this one
  if (type === 'email' && !EMAIL_RE.test(v)) return 'Enter a valid email address, e.g. name@example.com';
  if (type === 'tel') {
    if (!PHONE_RE.test(v)) return 'Enter a valid phone number, digits only';
    const digits = digitsOf(v);
    if (digits.length < 10 || digits.length > 15) return 'Enter a valid phone number (10–15 digits)';
  }
  if (type === 'number' && Number.isNaN(Number(v))) return 'Enter a number';
  return '';
}
