/**
 * Frontend feature flags.
 *
 * Single source of truth for switching whole subsystems on/off in the UI.
 */

/**
 * Chat / Messages subsystem.
 *
 * DISABLED 2026-09-02. The chat backend is known-broken and deliberately turned
 * off (security audit finding A10 — an unauthenticated identity bootstrap). This
 * flag removes every chat entry point from the UI: the "Messages" sidebar items
 * are filtered out (see mocks/navigation.ts) and the /messages and
 * /learner/messages routes redirect away (see router/config.tsx).
 *
 * To re-enable chat, flip this one constant to `true`. Do NOT re-enable without
 * the backend rebuild described in SECURITY_AUDIT.md (A18) — turning the UI back
 * on against the current backend reopens A10.
 */
export const CHAT_ENABLED = false;

/**
 * Learner feedback workspace.
 *
 * All feedback types are available during local development. Production
 * releases Live Sessions Feedback only; the API enforces the same scope.
 */
export const LEARNER_FEEDBACK_ENABLED = import.meta.env.DEV;

/** Release assigned Live Sessions Feedback in production as well as locally. */
export const LEARNER_POST_LECTURE_FEEDBACK_ENABLED = true;
