# Enrolment feature-branch schema rollout checklist

This is a preparation checklist, **not** approval to change a shared database.
No script here should be run against a shared or production database as part of
PR preparation. Keep the application code off the live service until the schema
steps below have been reviewed and completed through the team's normal change
process.

## Before the rollout

- Confirm the target host, database name, schema, owner, backup/restore point,
  and an isolated copy for rehearsal. Never rely on a local `.env` to identify
  the target. Rehearse with synthetic data or an approved isolated copy.
- Compare existing column types, constraints, indexes, and table names with each
  script and `schema.sql`. `IF NOT EXISTS` does **not** repair a column with the
  wrong type or constraint. Stop for a reviewed migration if they differ.
- Verify the current `enrolment.eligibility_form`, `enrolment."Created_users"`,
  `enrolment."Wizard_Ilr_Learner_Details"`, and wizard-layout tables exist where
  required. Confirm the application database role may perform the approved DDL.
- Keep `ELIGIBILITY_WORKPLACE_PREVIEW_ENABLED=false` in live settings; `DEBUG`
  must remain false. Do not enable local ILR navigation preview flags live.

## Schema-first order

Run each script as a separately reviewed transaction on the **verified target**,
after a backup and successful rehearsal. These scripts are additive; existing
answers, signatures, and application decisions are not rewritten.

1. `eligibility_submission_fields.sql`, `eligibility_structured_addresses.sql`.
   The submission script already adds
   `expected_leaving_date`; `eligibility_expected_leaving_date.sql` is an
   idempotent standalone alternative, not an additional dependency.
2. `eligibility_workplace_phase2a.sql`, then
   `eligibility_workplace_phase2b.sql`. Phase 2B depends on the earlier
   workplace columns/statuses. Keep workplace preview disabled in live use.
3. `eligibility_learner_links.sql`, after both `eligibility_form` and
   `"Created_users"` exist. This adds the provenance/invitation-recovery table;
   it does not backfill historical applications.
4. `ilr_health_and_hours.sql` and `ilr_sign_lock.sql`, after
   `"Wizard_Ilr_Learner_Details"` exists. Both are needed before serving the
   ILR health/working-hours or Sign + Next code. Neither infers a lock from an
   older signature.
5. Main's wizard-version feature also requires the additive layout metadata
   and `"Wizard_Layout_Assignments"` table. On an isolated copy, review
   `apply_wizard_form_layout_table --dry-run` and its submitted-learner
   reference backfill. With change approval, run the management command before
   serving version-pinned wizard requests; do not rely on the publish endpoint
   to perform first-use DDL. The fresh-install `schema.sql` has the same table
   and columns. The command may assign the then-current default layout version
   to historical submitted learners, so inspect the proposed effect first.

The order of steps 3 and 4 is independent once their base tables exist; they
are listed after Eligibility's dependent sequence to simplify review. Verify
that each required table/column and constraint exists before switching code.

## Compatibility and recovery

- **Code before schema is unsafe.** New Eligibility writes, linked invitations,
  ILR finalisation/health writes, and version-pinned wizard requests can fail if
  their columns or tables are missing. Deploy the approved additive schema
  first, then application code, then smoke-test Apprenticeship and Commercial
  flows with synthetic learners. The old code can continue to run with nullable
  additive columns present.
- Back up and record the pre-change schema/data state. If an application rollout
  fails, roll back the application first and leave additive columns/tables in
  place while preserving any newly written learner data and signed snapshots.
  Do **not** drop a column/table or reverse a finalised ILR lock to recover.
  A database restore requires a separate, reviewed plan for reconciling writes
  made after the backup.
- Check the live error rate, invitation delivery state (without sending test
  mail), saved ILR snapshots, account-settings read-only permissions, and
  published wizard/version resolution after deployment. Do not enable optional
  workplace screening as part of this merge.

No SQL or migration was executed against a shared or production database while
preparing PR #9.
