import { describe, expect, it } from 'vitest';
import { PLAN_EDITABLE_STATUSES } from "./page.helpers";

/**
 * Which learners an enrolment officer may still change the plan for.
 *
 * A policy decision rather than a detail, so it is asserted against the real
 * constant: editing used to be limited to Delivery, which meant a plan could
 * not be corrected once teaching began without moving the learner backwards.
 */
describe('plan editability by programme status', () => {
  it('allows editing during planning', () => {
    expect(PLAN_EDITABLE_STATUSES).toContain('Delivery');
  });

  it('allows editing once the learner is being taught', () => {
    // The change requested: modules get added, swapped and corrected
    // mid-programme.
    expect(PLAN_EDITABLE_STATUSES).toContain('Active');
  });

  it('allows editing for a paused learner', () => {
    // Same reasoning as Shift module, which already includes On break: a
    // paused learner is still on a programme.
    expect(PLAN_EDITABLE_STATUSES).toContain('On break');
  });

  it('keeps finished and unstarted plans read-only', () => {
    // Withdrawn and Completed plans are history; Onboarding has not been
    // agreed yet. Opening those read-only is deliberate, not an oversight.
    for (const status of ['Withdrawn', 'Completed', 'EnteredEpa', 'Onboarding', 'NonStarter']) {
      expect(PLAN_EDITABLE_STATUSES).not.toContain(status);
    }
  });

  it('treats an unknown or missing status as read-only', () => {
    // Fail closed: a status nobody has considered must not become editable.
    for (const status of ['', 'Something New']) {
      expect(PLAN_EDITABLE_STATUSES.includes(status)).toBe(false);
    }
  });
});
