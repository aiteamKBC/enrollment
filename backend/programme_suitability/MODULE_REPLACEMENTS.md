# Deleted LMS deliveries

Current saved academic Learning Plan reads can resolve one replacement delivery
after its original LMS module is physically or softly deleted. This applies to
fixed programme plans and confirmed active-pathway PCP plans. It does not discover
new academic modules or allocate groups.

The shared resolver verifies the confirmed programme, saved home cohort, saved
group, pinned academic definitions and (for PCP) original allocation evidence.
Candidates must be live deliveries in that exact programme/cohort/group, with
valid recorded dates and an exact normalized academic title or an already
administrator-confirmed alias. Similar titles are insufficient. An existing
active original is never substituted. Multiple matches, moved originals or
customised module weeks require review; missing replacements show a waiting
notice. Connection/permission failures never prove deletion.

The current Learning Plan response uses the replacement's actual ID, title,
dates and recorded module OTJ total. Missing hours remain pending. The Step 6A
learner-specific activity reader uses that same replacement only after validating
the original saved confirmation. No activities from the deleted module are copied.

This is a read-time projection, not a database migration: the original saved
confirmation and PCP allocation ledger remain unchanged. Normal plan saves can
round-trip the effective replacement ID while retaining its original confirmation
reference. No seats are consumed, groups/cohorts reassigned, or old progress and
submissions transferred. Previously issued/signed documents are untouched. No PDF
generation changes are included; future document integration must explicitly use
this learner-scoped resolver before capturing a new draft snapshot.

All LMS access uses the existing approved read-only role with bounded,
parameterized SELECT queries. No migrations, permission changes or live writes
are performed by this feature. Refresh/reopen the plan after replacement modules
have been fully configured in LMS to obtain the current projection.
