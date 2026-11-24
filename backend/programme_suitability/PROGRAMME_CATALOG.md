# Programme Structure catalogue

The Programme Structure sidebar link opens the Enrolment-owned catalogue at
`/users/programme-structure`. Its first request loads the live programme list.
Missing catalogue migrations show a setup-required state. Route errors, database
failures and invalid responses are reported separately. The legacy publication page
remains available internally at `/users/programme-structure/legacy`.

For visual review, select **Open interactive preview** on the setup-required screen
or visit `/users/programme-structure/preview` directly. Both require the Vite
development server and a signed-in Enrolment admin session. The preview starts empty.
**Add to preview** and **Update preview** modify only
the open page's in-memory state. You can exercise programmes, cohorts, groups, module
templates and scheduling, KSBs, weekly topics, and pricing without catalogue API requests,
browser storage, or migrations. Reload the page to clear the working data. Both staff
pickers start empty. Use **Add preview skills coach** or **Add preview tutor** to enter
sample details and test selection with a temporary UUID. Preview staff exist only in
the open page and are never read from or written to the staff directory.

Cohorts, groups and scheduled modules can be started from the main catalogue or from
a parent page. Parent pages preselect their own scope; changing a programme clears the
cohort, group and any programme-specific module revision. Changing a cohort clears the
group. The preview reads in-memory parents and the real catalogue reads saved records.

The real catalogue and preview share the same forms, navigation and immediate calculator.

Curriculum has Module Templates, KSB Library and KSB Mapping tabs. The mapping
uses the latest template links. Each KSB is counted once when it is covered by
any displayed template. Search and filters change the exported rows and leave
the programme totals visible. Edit assignments saves just the selected module's
KSB links under its current record revision. Scheduled deliveries keep the KSB
snapshot from their pinned revision.
The live API remains authoritative for persisted validation, concurrency and saved
quotes. The browser calculator mirrors its decimal penny-allocation policy with integer
arithmetic so input changes can show reconciled results before anything is saved.

## Ownership and dates

- Programme → cohort → group → scheduled delivery. A delivery references one reusable
  programme curriculum module; weeks, counts, weekly topics and KSB coverage live on the
  module, while the schedule and tutor live on the delivery. Each delivery pins an
  append-only module revision containing the name, weeks, counts, KSB labels and topics
  at that revision. Editing a template does not change any existing deliveries. A future
  delivery can be explicitly switched to a different revision; once it starts, its
  recorded revision cannot change. KSB and topic IDs remain stable across versions.
  Editing a linked KSB refreshes the latest revision of its active manual templates,
  not their pinned deliveries. Staff UUIDs resolve against
  the existing Enrolment Staff Directory and are validated against active Coach/Tutor
  access, including secondary grants. An assignment may be left unset while planning.
  A saved staff assignment that becomes unavailable stays in place until changed.
- Each cohort has its own planned duration in months. Training end is start plus training
  months, minus one day. Apprenticeship
  end is start plus training and EPA months, minus one day. Both calendar offsets use
  the original start day, clamped to the last valid day of the target month before
  subtracting the inclusive day. EPA is entered in months and can be zero. The two end
  dates can be overridden and reset independently. A manual training end shifts an
  automatic apprenticeship end to the day after training plus the EPA months, minus
  one day. Zero EPA uses the training end. An apprenticeship override remains fixed.
  Editing the planned duration recalculates automatic dates for that cohort. Existing
  catalogue cohorts without a saved duration retain their original training end as the
  reset value and use their original EPA units and calendar rules until an administrator
  enters a planned duration.
  The module form suggests an inclusive end date from the selected revision. A
  saved explicit end date may differ from that suggestion. Both dates must fit
  inside cohort training, and changing cohort dates cannot exclude an active
  delivery. Existing schedules without an explicit end keep the date calculated
  from the pinned revision.
  Group times are local, with weekday 1 = Monday and 7 = Sunday. The UI displays HH:mm
  and preserves stored seconds for times the administrator leaves unchanged.
- UUIDs are internal. `source_system`, optional `external_id`, `source_revision` and
  `last_synced_at` are separate read-only metadata in the manual API. Non-empty external
  IDs are unique per source and entity type. Source-owned records are read-only in the
  manual API. A future adapter must map source identities,
  use the programme as the write transaction boundary, and explicitly define ownership
  and conflict handling. The Django-only functions in `catalog_services.py` validate
  costs, dates, staff roles, parent scoping, module/KSB/topic links and delivery revision
  selection. An importer should call the same rules after parsing and append rather
  than update snapshots. There is no LMS client, schema assumption or import here.
- DELETE archives, not deletes. Ancestor archival makes descendants read-only without
  changing their records. Retire live deliveries before archiving a module; archived KSBs
  remain on linked templates but cannot be added to new templates. Removed topics are
  archived by week; re-adding a week keeps its record ID.

## HTTP contract

Base: `/enrolment_api/programme-structure/catalog/`. Enrolment staff sessions and the
existing CSRF handshake are required. JSON uses snake_case, ISO dates and UUID strings.

| Path under the base | Methods and response |
| --- | --- |
| `readiness/` | GET `{state: "ready" \| "schema_not_ready"}`. Requires the catalogue migrations through `0018`. |
| `programmes/` | GET `{programmes}`; POST programme fields → programme (201) |
| `programmes/{programme_id}/` | GET `{programme, cohorts, groups, modules, ksbs, deliveries}`; PATCH programme fields; DELETE → 204 |
| `programmes/{programme_id}/{resource}/` | GET `{resource: [...]}`; POST → record (201). Resources: `cohorts`, `groups`, `modules`, `ksbs`, `deliveries` |
| `programmes/{programme_id}/{resource}/{record_id}/` | GET record; PATCH writable fields; DELETE → 204 |
| `programmes/{programme_id}/pricing/?group_id={group_id}` | GET `{pricing, written_agreement_costs}`; omit group ID for allocations without rates |
| `staff/` | GET `{coaches: [{id, name, email}], tutors: [{id, name, email}]}` |

All mutable records return a numeric `revision`. PATCH and DELETE require
`If-Match: "N"`, where `N` is the revision read by the editor. The check and write run
under the same programme lock. Missing revisions return 428; a stale revision returns
409 with `code: "catalog_revision_conflict"` and `current_revision`. Reload before
resubmitting. Topics and KSB links are part of the module revision rather than separate
editable records.

POST `{"ksbs":[{"code":"K1","description":"..."}]}` to the programme's
`ksbs/` collection for a bulk import. The import uses the same row validation
and programme lock as single-record writes, and duplicate codes reject the
whole batch. The mapping export and the two-column library CSV serve separate
workflows.

The Catalogue programme API does not accept or return a duration. Create a cohort with
`name`, `start_date`, `training_duration_months` (1–120) and `epa_period` (months). Supply
`training_end_override` or `apprenticeship_end_override` as ISO dates to set manual
values. On PATCH, omit a field to keep its mode or send `null` to reset it. The API
returns the effective dates as `training_end_date` and `apprenticeship_end_date`, the
reset baseline as `automatic_training_end_date`, and the nominal duration in months
for schedules with a recorded cohort duration. The UI reports elapsed training days
when a training end is overridden or a cohort has no recorded duration.
PATCH `training_duration_months` to revise a cohort’s planned duration. Supply an
explicit `epa_period` in months when converting a legacy day- or week-based schedule.
Leave the duration out of a PATCH to retain the stored value and schedule mode.
On creation, a group takes `cohort_id`. A scheduled delivery takes `group_id`,
`start_date` and an eligible `tutor_id`. Pass `end_date` to save an explicit end
date. A null end date uses the inclusive date calculated from the pinned revision
and the start date. Omit the field on PATCH to retain its mode. Responses expose
the effective date as `end_date` and the stored override as `end_date_override`.
Existing tutor assignments and legacy unassigned records
are retained when a PATCH omits `tutor_id`.
The delivery form submits `cohort_id` as a check that the group belongs to the
selected cohort. Programme scope comes from the URL. Both are derived from the group.
Send `module_id` with an optional `module_revision_id` to use an existing template,
or submit `new_template` with the usual module fields to create and schedule
its first revision in a single transaction. An unchanged delivery keeps its pin.
Delivery reads include the pinned `curriculum`; module reads include `current_revision`.
Module accepts `ksb_ids` and `weekly_topics: [{week, content}]`,
each replaced as a collection when supplied. Missing delivery counts are `null`,
not zero. The payload cannot reparent a record or write source metadata. Validation
returns 400 with `{error, fields}`; out-of-scope IDs return 404 on record reads and
invalid relationship IDs return 400 on writes; archive conflicts return 409.
When migrations are missing, live endpoints return 503 with
`code: "catalog_schema_not_ready"`; database failures use `catalog_database_error`.

## Pricing and existing consumers

Programme total and EPA are GBP per apprentice. The remainder is allocated 40/40/15/5
using decimal pennies, with the two training lines reconciling to rounded 80% of base.
EPA is separate. A rate uses the pinned module revisions for **one** group; counts
are never summed across parallel groups. Any missing component count leaves that
metric uncalculated; a zero denominator also returns `null`. Lecture duration defaults
to 2.50 hours and is explicit in both programme and quote.

The Written Agreement adapter maps the five supported allocations into existing line
labels, leaves other categories and balance due unassigned, and includes a versioned,
JSON-safe pricing snapshot. The pricing function can take an authorised learner total
in future without updating the master, but no override is exposed by the catalogue.
There is no automatic match from a catalogue programme/group to an existing learner:
enrolment, onboarding, learning plan, RPL and agreement issuance still use their
existing data sources. Existing issued documents retain their saved costs; when an
explicit learner-pricing binding is introduced, persist the adapter result in the
existing Written Agreement costs snapshot at issue rather than reading live prices.

## Migration

`0013_programme_catalog` adds catalogue tables, indexes and constraints. The additive
`0014_catalog_revisions` adds record revisions, snapshots the current catalogue modules
if any exist, and pins existing catalogue deliveries to those snapshots. It does not
modify wizard publications, learner records or documents. A catalogue already in use
before 0014 can carry forward only its current content, not reconstruct older edits.
`0015_cohort_training_duration` adds a nullable duration snapshot without rewriting
existing cohorts. `0016_cohort_date_overrides` adds two nullable end-date overrides.
`0017_catalog_programme_optional_duration` makes the former programme duration column
nullable and retains existing values. `0018_catalog_delivery_end_date` adds a
nullable end-date override and enforces date order when it is present. Existing
delivery dates continue to use their pinned weeks. The live catalogue requires
all six migrations. They create no seed data.

To activate live persistence, load the current checkout in the Django process used by
the frontend, restarting a non-reloading server such as Daphne. On the approved Enrolment
database, review the migration plan and apply the pending `programme_suitability`
migrations through `0018_catalog_delivery_end_date` on the `default` alias. Then check that
`readiness/` reports `ready` and `programmes/` returns the list in an authenticated
Enrolment staff session. Writes use the existing CSRF handshake.

To inspect schema consistency without applying anything:

```powershell
backend/.venv/Scripts/python.exe backend/manage.py makemigrations programme_suitability --check --dry-run --settings=enrollment_service.settings_test
```
