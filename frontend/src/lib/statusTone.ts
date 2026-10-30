// ============================================================================
// Semantic status colour.
//
// One table, so the same signal is the same colour on every screen. Before this
// existed the workspace held nineteen separate mappings and "on track" resolved
// to three different greens depending on which page you were reading.
//
// The rules the table encodes:
//
//  - Green / rose / red carry risk, and only risk.
//  - Rose is the "coming up, not a problem yet" band, which
//    needs to be visible without being alarming.
//  - Blue is a neutral fact about time — live now, scheduled, in progress. It is
//    deliberately not green: "scheduled" is not an achievement.
//  - Purple is reserved for interface state: the active tab, the current
//    selection, the primary action. It never encodes data, so a coach's eye is
//    never pulled to a brand colour when nothing is wrong.
//  - Grey is genuinely absent data, not a mild version of bad.
//
// Fills are fixed at the -50 / -700 / -200 scale. The -100 variants that used to
// appear alongside them read as a *different* status rather than the same one,
// which is the specific bug this file exists to prevent.
// ============================================================================

export type StatusTone =
  | 'positive'
  | 'caution'
  | 'critical'
  | 'upcoming'
  | 'info'
  | 'brand'
  | 'neutral';

export interface ToneStyle {
  /** Solid fill, for dots and progress bars. */
  dot: string;
  /** Tinted surface, for badges and soft panels. */
  bg: string;
  text: string;
  border: string;
  /** Ring colour, for avatars carrying state. */
  ring: string;
  /** Default icon for the tone; a caller may override it. */
  icon: string;
}

const TONE_STYLE: Record<StatusTone, ToneStyle> = {
  positive: {
    dot: 'bg-emerald-500',
    bg: 'bg-emerald-50',
    text: 'text-emerald-700',
    border: 'border-emerald-200',
    ring: 'ring-emerald-300',
    icon: 'ri-check-line',
  },
  caution: {
    dot: 'bg-amber-500',
    bg: 'bg-amber-50',
    text: 'text-amber-700',
    border: 'border-amber-200',
    ring: 'ring-amber-300',
    icon: 'ri-error-warning-line',
  },
  critical: {
    dot: 'bg-red-500',
    bg: 'bg-red-50',
    text: 'text-red-700',
    border: 'border-red-200',
    ring: 'ring-red-300',
    icon: 'ri-alarm-warning-line',
  },
  upcoming: {
    dot: 'bg-primary-500',
    bg: 'bg-primary-50',
    text: 'text-primary-700',
    border: 'border-primary-200',
    ring: 'ring-primary-300',
    icon: 'ri-calendar-event-line',
  },
  info: {
    dot: 'bg-primary-500',
    bg: 'bg-primary-50',
    text: 'text-primary-700',
    border: 'border-primary-200',
    ring: 'ring-primary-300',
    icon: 'ri-information-line',
  },
  brand: {
    dot: 'bg-primary-500',
    bg: 'bg-primary-50',
    text: 'text-primary-800',
    border: 'border-primary-200',
    ring: 'ring-primary-300',
    icon: 'ri-focus-3-line',
  },
  neutral: {
    dot: 'bg-foreground-300',
    bg: 'bg-background-100',
    text: 'text-foreground-500',
    border: 'border-foreground-200',
    ring: 'ring-primary-100',
    icon: 'ri-pause-circle-line',
  },
};

export function toneStyle(tone: StatusTone): ToneStyle {
  return TONE_STYLE[tone];
}

/**
 * The status words the coach API actually emits, mapped to a tone. Matching is
 * case- and separator-insensitive because the same status arrives as "At Risk",
 * "at-risk" and "atrisk" from different endpoints.
 *
 * Unknown values resolve to `neutral` rather than guessing, so a new backend
 * status shows up as plainly uncoloured instead of quietly claiming to be good.
 */
const STATUS_TONE: Record<string, StatusTone> = {
  // positive
  ontrack: 'positive',
  complete: 'positive',
  completed: 'positive',
  approved: 'positive',
  accepted: 'positive',
  confirmed: 'positive',
  validated: 'positive',
  active: 'positive',
  green: 'positive',
  signed: 'positive',

  // caution
  needattention: 'caution',
  needsattention: 'caution',
  duesoon: 'caution',
  needsscheduling: 'caution',
  // "needs-schedule" is a distinct wording of the same status a few pages use
  // (normalizes to "needsschedule", one letter short of "needsscheduling"
  // above) — found missing when Monthly Cycle's coaching-delivery status fell
  // through to `neutral` silently instead of the amber it should have been.
  needsschedule: 'caution',
  notscheduled: 'critical',
  pending: 'caution',
  awaiting: 'caution',
  awaitingsignature: 'caution',
  onbreak: 'caution',
  break: 'caution',
  partial: 'caution',
  referred: 'caution',
  amber: 'caution',

  // critical
  atrisk: 'critical',
  overdue: 'critical',
  highrisk: 'critical',
  declined: 'critical',
  rejected: 'critical',
  cancelled: 'critical',
  missed: 'critical',
  escalated: 'critical',
  red: 'critical',

  // info — a neutral fact about time, not an achievement
  live: 'info',
  scheduled: 'info',
  inprogress: 'info',
  booked: 'info',
  submitted: 'info',

  // neutral
  withdrawn: 'neutral',
  inactive: 'neutral',
  notstarted: 'neutral',
  readytoenrol: 'brand',
};

function normalizeStatus(value?: string | null): string {
  return (value || '').trim().toLowerCase().replace(/[^a-z]/g, '');
}

/** Tone for a backend status string. Unknown values are `neutral`. */
export function statusTone(value?: string | null): StatusTone {
  return STATUS_TONE[normalizeStatus(value)] || 'neutral';
}

/** Convenience: the resolved style for a backend status string. */
export function statusStyle(value?: string | null): ToneStyle {
  return toneStyle(statusTone(value));
}

/**
 * Progress bars shade by how healthy the figure is rather than by which metric
 * it belongs to, so a coach can read a column of bars without a legend.
 *
 * The mid band is brand purple rather than a fourth risk colour: "in progress
 * and fine" is not a warning, and using amber there made every healthy learner
 * look like a problem.
 */
export function progressTone(percent: number | null): string {
  if (percent === null) return 'bg-foreground-200';
  if (percent >= 85) return 'bg-emerald-500';
  if (percent >= 60) return 'bg-primary-500';
  if (percent >= 40) return 'bg-amber-500';
  return 'bg-red-500';
}

/**
 * Tone for a date relative to today, in days. Used by every "next review",
 * "due", "last contact" cell so overdue never looks merely late on one screen
 * and urgent on another.
 */
export function dueTone(daysAway: number | null, soonWithin = 30): StatusTone {
  if (daysAway === null) return 'neutral';
  if (daysAway < 0) return 'critical';
  if (daysAway <= soonWithin) return 'caution';
  return 'neutral';
}
