"""The single rule that decides whether a learner progress row counts as
achieved KSB delivery.

The completion model
--------------------
A progress row records that a learner *did* something. Whether it succeeded is
carried by one nullable boolean, ``learner_progress_entries.passed``:

* graded activity (``kind = 'quiz'``) sets it explicitly — ``True`` pass,
  ``False`` fail;
* an ungraded completion (``component``, ``video``, …) never sets it, so it
  stays ``NULL`` and the row's existence *is* the completion.

That is the only completion model in the LMS; this module states it once rather
than adding a second one.

Why this module exists
----------------------
Reporting used to lean on the shape of the data instead of the rule: the only
check was ``kind == 'quiz' and passed is not True``, and it was safe purely
because no failed row happened to carry Component lineage. A graded Component
— or any future write that sets ``passed=False`` alongside a ``component_ref``
— would have credited its authored KSBs on a failed attempt, with valid
lineage and valid KSB snapshots to make it look legitimate.

What this does **not** do
-------------------------
Nothing here hides or deletes activity. A failed attempt stays in
``learner_progress_entries``, stays in the learner's history, and stays visible
to the Coach. It simply stops counting as achieved KSB delivery.

Deliberately Python-only
------------------------
Reporting queries select ``kind`` and ``passed`` and apply the rule here rather
than repeating it as a SQL predicate. Every one of them already aggregates in
Python, so a second implementation would buy nothing and could drift from this
one. One rule, one place.
"""

# Kinds whose row is a *graded* attempt: achievement requires an explicit pass.
# Everything else records a completion, so ``passed`` stays NULL and the row is
# the achievement. Keep this the single place the distinction is made.
GRADED_PROGRESS_KINDS = frozenset({'quiz'})

ACHIEVED = 'achieved'
FAILED = 'failed'
INCOMPLETE = 'incomplete'


def _kind(value):
    return str(value or '').strip().lower()


def progress_counts_as_achieved(kind=None, passed=None):
    """Does this progress row contribute to achieved KSB progress?

    ``passed is False`` never counts, whatever the kind. A graded kind must
    have passed outright — a ``NULL`` there is an attempt that was never
    resolved, not a completion.
    """
    if passed is False:
        return False
    if _kind(kind) in GRADED_PROGRESS_KINDS:
        return passed is True
    return True


def progress_record_counts_as_achieved(record):
    """``progress_counts_as_achieved`` for a serialised progress/activity dict."""
    if not isinstance(record, dict):
        return False
    return progress_counts_as_achieved(kind=record.get('kind'), passed=record.get('passed'))


def progress_achievement_status(kind=None, passed=None):
    """Why a row does or does not count, for reporting payloads.

    ``'achieved'`` | ``'failed'`` (explicitly not passed) | ``'incomplete'``
    (a graded attempt with no recorded outcome).
    """
    if passed is False:
        return FAILED
    if _kind(kind) in GRADED_PROGRESS_KINDS and passed is not True:
        return INCOMPLETE
    return ACHIEVED
