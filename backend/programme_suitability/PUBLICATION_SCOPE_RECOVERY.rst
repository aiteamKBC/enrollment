Explicit Programme Structure publication-scope recovery
======================================================

Prepared and tested locally, 13 November 2026. NOT applied to live data.

Root cause and existing workflow
-------------------------------

``read_current(cursor, 'apprenticeship')`` prioritises an exact learner-kind
match before version number. Therefore active apprenticeship v16 takes
precedence over generic (NULL learner-kind) v23. v16 has no canonical
``programmeStructure``; v23 does. The shared draft is based on v23, revision 12.
These identifiers were rechecked with SELECT-only queries in a read-only
transaction. No LMS access was used.

The normal publisher correctly rejects base 23 against effective version 16.
Changing the expected version in the HTTP payload cannot fix that safely.
The existing explicit ``rebase_programme_structure_draft`` command also cannot
resolve this case with its normal three-way merge: the actual saved data
conflicts at ``$.programmes[ME_L4].cohorts`` because v16 lacks canonical cohorts.
The Admin Curriculum identity reconciliation action is not a publication rebase
and also rejects the stale base. No supported UI-only repair was found.

Narrow technical correction
---------------------------

The existing recovery command now accepts the optional, explicit
``--preserve-content`` mode. Its default three-way behavior is unchanged.
This new mode only permits an active generic source publication with canonical
data to be reconciled to an active apprenticeship target without canonical
data. It never changes publication resolution or adds implicit fallbacks.

The existing transaction, publication-table lock, draft-row lock, exact expected
base/revision/current-version checks, verified backup, atomic save and reload
verification remain in place. A concurrent publication or draft edit rejects
the operation. Target canonical data, incompatible programme-wide legacy
projections, unrelated calendar changes or normalizer changes are rejected.

The pure preparation helper validates the saved structure with the existing
preflight, legacy compatibility guard and official normalizer. It verifies
that the full future apprenticeship layout differs from the effective target
only by adding the exact saved canonical structure. It does not merge or edit
academic content. Existing publication preflight/concurrency checks remain
unchanged and must pass again when publication is separately approved.

No apply flag means no database write, although the existing command still
locks records during validation and saves a local recovery backup. Neither
the dry-run nor apply management command was executed against live data in
this task. Only the pure preparation/normalization functions were simulated
using SELECT-only input data. Apply was exercised only in disposable tests.

Complete content comparison for the inspected data
-------------------------------------------------

Proposed recovery of the saved draft:

* base_version: 23 -> 16.
* configuration._draftRevision: 12 -> 13.
* updated_at changes only if an approved apply is performed.
* Every other configuration field, including version/hash, remains equal.

Full normalized future publication versus v16:

* Add programmeStructure, exactly the saved draft with revision metadata 13.
* No other layout key/value changes. Wizard steps, texts, programmeSuitability,
  programmeModules and skillsRadarCatalogues remain identical. No intakeCalendar
  is added or changed.

Full normalized future publication versus v23:

* programmeStructure._draftRevision: 9 -> 13.
* February assignmentPeriod.startDay: 15 -> 16 for ME_L4, MM_L6, APM_L4 and
  PCP_L6. These four changes ALREADY EXIST in saved draft revision 12; recovery
  does not introduce or alter them.
* programmeStructure.version changes from
  5c701baa7e46f0a7480d7e7b9a50834268252e1d0435ec73823b7602857b733c to
  daeb2cd16d4faed6a0b2cccd68201acfaee16a40aae7fc6376ec9b048cb1c7bc,
  the version ALREADY stored in the saved draft.
* No other content differences. In particular, all programme identities,
  cohort IDs/names/statuses/dates, modules, PCP pathways and KSBs remain equal.

The future row would have a newly allocated ID/timestamp/actor and explicit
apprenticeship learner kind, unlike v23's NULL kind. Its ID is not guaranteed
to be 24 because another publication may occur first. Old rows are not updated.
The direct JSON-pointer comparison is available in the ignored local artifact
``.cache/publication-scope-simulation.json``. Simulation inputs remained equal
after preparation.

Publishing side effects requiring separate approval
---------------------------------------------------

The existing publisher, which was NOT modified, would:

* Insert the new default apprenticeship Wizard Layout row, preserving v16/v23.
* Update the shared draft base to the new row and increment its revision again
  (13 -> 14 if nothing else changes). The published snapshot would retain 13.
* Run ``ensure_layout_table`` and ``apply_plan``: idempotent CREATE/ALTER/index
  statements, not a Django migration command. The inspected layout plan uses
  Wizard_Introduction and Wizard_Cv_Job; all eight named custom columns and both
  indexes were present in the preceding read-only audit. DDL still executes
  and can acquire locks; it must not be described as a SELECT-only operation.
* Read/lock the five matching Curriculum cohorts. Since v16 has no canonical
  cohort status entries, all five are treated as newly published and receive
  the existing status/name UPDATE path, even though their current values match
  the draft's ``planned`` status and names. The Curriculum history recorder is
  called; it suppresses identical revisions where an identical history already
  exists, so exactly five new history rows are not guaranteed.
* Propagate names to learner placements only if names have changed. No rename
  is indicated by the inspected data, but any concurrent name/status difference
  requires a fresh review of these side effects before publication.

No historical learner assignment or pin UPDATE is part of publication. Existing
unpinned apprenticeship learners can read the new default; this is not limited
to newly created accounts. Their raw assessment, programme confirmation and
Enrolment Details records are not rewritten. Existing APPLIED synchronizations
remain idempotent and are not replayed by publication. Commercial resolution
continues to use its existing applicable version; generic v23 is not rewritten.

Rollout flags remain disabled. Enabling cohort-aware module reads later has
separate effects and requires its own review/approval. Existing explicit pins
remain authoritative, including older versions without canonical cohorts.

Steps for an administrator AFTER separate approval
--------------------------------------------------

1. Recheck effective apprenticeship version, shared base/revision and saved
   academic content. Stop if they differ from the reviewed 16 / 23 / 12 state;
   do not blindly replace the expected numbers to force the operation.
2. From the repository root, run the existing recovery command in preview mode
   with a fresh private backup directory::

     .\backend\.venv\Scripts\python.exe backend\manage.py rebase_programme_structure_draft --base-version 23 --revision 12 --published-version 16 --backup-dir .cache\approved-scope-recovery --preserve-content

3. Review the recovery backup and unchanged content. Only after explicit
   approval to UPDATE the shared draft, repeat that exact command with
   ``--apply``. This changes draft bookkeeping only; it does not publish or
   enable features. Verify base 16, revision 13 and unchanged content.
4. Reload Admin > Programme Structure. Confirm no unsaved editor changes,
   base/revision and all saved academic content. Use Publish Preview and review
   its compatibility checks and the side effects listed above.
5. Obtain separate approval for the publishing flag and actual publication.
   Enable only the approved publishing setting, refresh/review, then use
   Publish > Confirm Publish to append the new apprenticeship version.
6. Verify the new version's scope, full layout comparison and unchanged old
   rows/pins before considering any cohort-learner or automatic-assignment flag.
   Feature activation and historical reconciliation are separate approvals.

Remaining data prerequisites are unchanged: all four programmes have February
2027 cohorts, only ME has June 2027, and the inspected structure has no October
cohort. Missing targets must remain pending. No dates/cohorts may be invented.
Saved draft February windows begin October 16 and preserve the agreed cutoff.
Older learner pins without canonical structure are not repaired by this work.

Verification and safety
-----------------------

Isolated SQLite tests cover v16 exact-kind versus v23 generic precedence,
content-preserving preview/apply, rejection of the old three-way scope conflict,
concurrent draft/publication changes, legacy-content conflicts, target-canonical
conflicts, historical pin preservation and unpinned-default effects. The existing
publication, shared-draft authentication and HTTP regressions also run. Tests
block external networking and use no real environment credentials.

Only draft_rebase.py, its management-command option, the new publication_scope.py
helper, focused tests and this document were changed in this task. Programme
Structure academic services/editor, publisher, publication resolver, confirmation
and cohort assignment services, compliance, Training Plan, environment files
and all live records remain unchanged. No flags, migration, publication, branch
operation, commit or push were performed. LMS was not accessed.
