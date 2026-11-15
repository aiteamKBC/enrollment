# LMS module mapping approval — draft v1

**UNAPPROVED — documentation only. No runtime use or mappings approved.**

Review date: 2026-11-14 (Europe/London). Academic source: verified apprenticeship v24 fixture. Existing 28-row review reused; only the four verified LMS programme IDs were refreshed through the existing SELECT-only reader. Eligible rows and fields were unchanged.

## Approval counts

8 proposed candidate mappings; 16 ambiguous; 4 unavailable; **0 approved**. These are 28 pathway-specific definitions (23 unique programme/module identities). **19 definitions / 15 unique modules have real future candidate deliveries (21 distinct offerings); 0 have approved academic-to-LMS bindings.**

A = APPROVE CANDIDATE (recommendation requiring owner confirmation); H = NEEDS HUMAN REVIEW; N = NO VERIFIED MATCH. A is not an approval and is not based on name similarity alone. Owner must confirm syllabus/qualification identity for every A/H row. Future means start_date > review UK date, not end_date > date. Future schedules still need academic approval and learner scope checks.

## Programme scope

| Enrolment | Exact LMS programme ID |
|---|---|
| ME_L4 | `PROG-ME-L4` |
| MM_L6 | `PROG-MM-L6` |
| APM_L4 | `PROG-20260907141619067932` |
| PCP_L6 | `PROG-PCP-L6` |

## Owner approval table

Every row below has exact title aliases and individual delivery/cohort/group/date evidence in the adjacent versioned JSON, keyed by review ID. The next register shows all distinct future candidates once; already-started candidates remain in JSON for historical review only. No inactive-pathway selections are combined.


### Marketing Executive — Level 4

| Row / academic ID / exact academic title | Path / class / credits | Exact LMS title candidates | Future candidates | Decision / required owner decision |
|---|---|---|---|---|
| M01 · `marketing-impact` · Marketing Impact and Planning | — / fixed / — | `G2-Juliane– Impact &amp;Planning`; `Impact& Planning`; `Marketing Impact and Planning` | 1; in v24 home cohorts: 1 | **H** — Confirm which of the three exact title variants deliver the same Marketing Impact and Planning syllabus; approve each alias explicitly, not the wording alone. |
| M02 · `social-media` · Social Media | — / fixed / — | `Social Media` | 1; in v24 home cohorts: 0 | **A** — Single exact candidate title within the verified programme; owner must confirm authoritative academic identity before approval. Similar wording is not evidence of equivalence. |
| M03 · `martech` · Marketing Technology (MarTech) | — / fixed / — | `Martech`; `Martech - Fri`; `Martech - Thur` | 1; in v24 home cohorts: 0 | **H** — Confirm Martech, Martech - Fri and Martech - Thur deliver the academic MarTech module; preserve separate day/group deliveries. |

### Marketing Manager — Level 6

| Row / academic ID / exact academic title | Path / class / credits | Exact LMS title candidates | Future candidates | Decision / required owner decision |
|---|---|---|---|---|
| M04 · `marketing-strategy` · Marketing Strategy and Planning | — / fixed / — | `Marketing Strategy&Planning` | 0; in v24 home cohorts: 0 | **A** — Single exact candidate title within the verified programme; owner must confirm authoritative academic identity before approval. Similar wording is not evidence of equivalence. |
| M05 · `customer-journey` · Customer Journey Optimisation | — / fixed / — | `Customer Journey Optimisation` | 1; in v24 home cohorts: 0 | **A** — Single exact candidate title within the verified programme; owner must confirm authoritative academic identity before approval. Similar wording is not evidence of equivalence. |
| M06 · `commercial-intelligence` · Commercial Intelligence | — / fixed / — | `Commercial Intelligence` | 1; in v24 home cohorts: 0 | **A** — Single exact candidate title within the verified programme; owner must confirm authoritative academic identity before approval. Similar wording is not evidence of equivalence. |
| M07 · `ai-marketing` · AI in Marketing | — / fixed / — | `AI In Marketing` | 1; in v24 home cohorts: 0 | **A** — Single exact candidate title within the verified programme; owner must confirm authoritative academic identity before approval. Similar wording is not evidence of equivalence. |

### Associate Project Manager — Level 4

| Row / academic ID / exact academic title | Path / class / credits | Exact LMS title candidates | Future candidates | Decision / required owner decision |
|---|---|---|---|---|
| M08 · `pmp` · Project Management Professional | — / fixed / — | None | 0; in v24 home cohorts: 0 | **N** — No verified PMP offering within the exact APM programme. Do not borrow PCP PMP or substitute Definition of Project Management / Last Month: Project Manager. |
| M09 · `ai-project-management` · AI in Project Management | — / fixed / — | None | 0; in v24 home cohorts: 0 | **N** — No verified AI in Project Management offering within the exact APM programme; PCP AI in Project Control is not a substitute. |

### Project Controls Professional — Level 6

| Row / academic ID / exact academic title | Path / class / credits | Exact LMS title candidates | Future candidates | Decision / required owner decision |
|---|---|---|---|---|
| M10 · `pmp` · Project Management Professional (PMP) | operational / mandatory / 2 | `Project Management Professional`; `Ray-Project Management Professional-June 2026` | 0; in v24 home cohorts: 0 | **H** — Confirm the plain and tutor-prefixed PMP titles deliver the required PMP qualification. All current candidate deliveries have already started; retain saved selections only. |
| M11 · `ai-project-control` · AI in Project Management/Control | operational / mandatory / 1 | `AI in Project Control` | 2; in v24 home cohorts: 0 | **H** — Confirm AI in Project Control covers AI in Project Management/Control for operational/strategic paths; do not reuse it for chartered ai-project-management without a separate decision. |
| M12 · `risk-apm` · Risk Management (APM) | operational / mandatory / 1 | `Risk Management` | 2; in v24 home cohorts: 0 | **A** — Owner must confirm Risk Management delivers the required APM Risk Management academic content/qualification; the omitted APM qualifier is not proven by the title. |
| M13 · `planning-control` · Project Planning and Control (APMG Qualification) | operational / optional / 1 | `PPC`; `Steve-Project Planning and Control(PPC)` | 2; in v24 home cohorts: 0 | **H** — Confirm PPC and the tutor-prefixed PPC title deliver the APMG qualification required by operational planning-control; do not infer equivalence with chartered pmo-planning-control. |
| M14 · `earned-value` · Earned Value Management (EVM) | operational / optional / 1 | `EVM`; `Steve-Earned Value Management(EVM)` | 2; in v24 home cohorts: 0 | **H** — Explicitly decide whether each EVM title satisfies the operational EVM and chartered APMG requirements separately; the shared academic ID does not prove identical qualifications across paths. |
| M15 · `pmi-sp` · PMI-SP | operational / optional / 1 | `PMI-SP Scheduling Professional` | 1; in v24 home cohorts: 0 | **A** — Confirm PMI-SP Scheduling Professional is the authoritative delivery of academic PMI-SP; retain exact programme and offering identities. |
| M16 · `pmp` · Project Management Professional (PMP) | strategic / mandatory / 2 | `Project Management Professional`; `Ray-Project Management Professional-June 2026` | 0; in v24 home cohorts: 0 | **H** — Confirm the plain and tutor-prefixed PMP titles deliver the required PMP qualification. All current candidate deliveries have already started; retain saved selections only. |
| M17 · `ai-project-control` · AI in Project Management/Control | strategic / mandatory / 1 | `AI in Project Control` | 2; in v24 home cohorts: 0 | **H** — Confirm AI in Project Control covers AI in Project Management/Control for operational/strategic paths; do not reuse it for chartered ai-project-management without a separate decision. |
| M18 · `risk-apm` · Risk Management (APM) | strategic / mandatory / 1 | `Risk Management` | 2; in v24 home cohorts: 0 | **A** — Owner must confirm Risk Management delivers the required APM Risk Management academic content/qualification; the omitted APM qualifier is not proven by the title. |
| M19 · `pmi-pmo` · PMI (PMO) | strategic / optional / 1 | `PMI PMO` | 2; in v24 home cohorts: 0 | **H** — Confirm PMI PMO delivers PMI (PMO); do not substitute the distinct Project Management Office component or its Ray-prefixed offering. |
| M20 · `portfolio` · Portfolio Management (APMG) | strategic / optional / 1 | `Portfolio Management`; `Stephen-Portfolio Management` | 2; in v24 home cohorts: 0 | **H** — Confirm Portfolio Management and Stephen-Portfolio Management deliver the APMG Portfolio Management requirement for each applicable pathway. |
| M21 · `successful-programmes` · Managing Successful Programmes | strategic / optional / 1 | `MSP`; `Managing Successful Programmes: Project Controls for Transformational Change` | 2; in v24 home cohorts: 0 | **H** — Confirm MSP and the long Project Controls for Transformational Change title deliver the same required Managing Successful Programmes syllabus/qualification. |
| M22 · `pmo-planning-control` · Project Planning and Control | chartered / mandatory / 1 | `PPC`; `Steve-Project Planning and Control(PPC)` | 2; in v24 home cohorts: 0 | **H** — Decide whether PPC also satisfies this chartered PMO component independently of operational planning-control. Keep distinct academic IDs even if deliveries are shared. |
| M23 · `pmo-stakeholders` · Stakeholder Engagement and Communication | chartered / mandatory / 1 | `Stakeholder Management & Reporting` | 0; in v24 home cohorts: 0 | **H** — Confirm Stakeholder Management & Reporting covers the full Stakeholder Engagement and Communication requirement; topic overlap alone is insufficient. Current delivery already started. |
| M24 · `pmo-risk-quality` · Risk, Quality and Issue Management | chartered / mandatory / 1 | None | 0; in v24 home cohorts: 0 | **N** — No verified delivery covering Risk, Quality and Issue Management; Risk Management alone does not establish full coverage. |
| M25 · `pmo-office` · Project Management Office | chartered / mandatory / 1 | `Ray-Project Management Office(PMO)` | 0; in v24 home cohorts: 0 | **H** — Confirm Ray-Project Management Office(PMO) delivers the chartered PMO component independently of PMI PMO. Its current delivery already started. |
| M26 · `ai-project-management` · AI in Project Management | chartered / mandatory / 1 | None | 0; in v24 home cohorts: 0 | **N** — No verified chartered AI in Project Management delivery. Decide academic equivalence to AI in Project Control explicitly or provide an authoritative separate delivery. |
| M27 · `portfolio` · Portfolio Management (APMG) | chartered / optional / 1 | `Portfolio Management`; `Stephen-Portfolio Management` | 2; in v24 home cohorts: 0 | **H** — Confirm Portfolio Management and Stephen-Portfolio Management deliver the APMG Portfolio Management requirement for each applicable pathway. |
| M28 · `earned-value` · Earned Value Management (APMG) | chartered / optional / 1 | `EVM`; `Steve-Earned Value Management(EVM)` | 2; in v24 home cohorts: 0 | **H** — Explicitly decide whether each EVM title satisfies the operational EVM and chartered APMG requirements separately; the shared academic ID does not prove identical qualifications across paths. |

## Cohort and group register

References below expand to exact LMS identities. The home-cohort register is v24 context, not an assignment to a particular learner. Only ME Feb 2027 currently has any matched future delivery in these five home cohorts: M01 Impact& Planning. ME June 2027, MM Feb 2027 and APM Feb 2027 have no reader-visible matched schedules; PCP Feb 2027 also has none. Do not borrow October 2026 schedules for fixed programmes.

| v24 programme | Home cohort name | Exact cohort ID |
|---|---|---|
| PROG-MM-L6 | Feb 2027 | `COHORT-20261110185121808654D6E19983AD79` |
| PROG-ME-L4 | Feb 2027 | `COHORT-20260917092025244736D2820D958B44` |
| PROG-ME-L4 | June 2027 | `COHORT-20261110195722888955DB66B4D8CC1C` |
| PROG-PCP-L6 | Feb 2027 | `COHORT-20261110185128015800C4494CA47EEB` |
| PROG-20260907141619067932 | Feb 2027 | `COHORT-20261110185125368519EE89CF7CF8DC` |

| Ref | Exact LMS programme / cohort ID | Cohort name |
|---|---|---|
| C01 | `PROG-ME-L4` / `COHORT-20260912125252180773` | October 2026 |
| C02 | `PROG-ME-L4` / `COHORT-20260917092025244736D2820D958B44` | Feb 2027 |
| C03 | `PROG-MM-L6` / `COHORT-20260912123900478752` | October 2026 |
| C04 | `PROG-PCP-L6` / `COHORT-20260912111441822802` | October 2026 |

| Ref | Cohort ref | Exact group ID | Group name |
|---|---|---|---|
| G01 | C01 | `GROUP-20260912125344504865` | G1 |
| G02 | C02 | `GROUP-20260917092119530122F3F181E8BFB0` | Group 1 |
| G03 | C03 | `GROUP-20260912125025166293` | G1 |
| G04 | C04 | `GROUP-20260912111638967115` | G1 |
| G05 | C04 | `GROUP-2026100110425434687520181B1A2448` | G2 |

## Distinct future candidate delivery register

Each row is a specific delivery, never a universal academic ID. G1 and G2 remain separate. All are UNAPPROVED; PCP compatibility outside the home cohort must be confirmed independently.

| Academic review rows | Exact LMS title | Offering ID | Cohort / group refs | Start → end |
|---|---|---|---|---|
| M01 | Impact& Planning | `MOD-202609170923319254807B020222EFAA` | C02 / G02 | 2027-02-01 → 2027-05-21 |
| M02 | Social Media | `MOD-20260912110325036606` | C01 / G01 | 2027-02-15 → 2027-05-30 |
| M03 | Martech | `MOD-20260912102543166910` | C01 / G01 | 2027-06-10 → 2027-09-15 |
| M05 | Customer Journey Optimisation | `MOD-20260912130240756878` | C03 / G03 | 2027-02-22 → 2027-05-30 |
| M06 | Commercial Intelligence | `MOD-2026100114185277472098BA44BEB889` | C03 / G03 | 2027-06-15 → 2027-10-04 |
| M07 | AI In Marketing | `MOD-20260912132513706682` | C03 / G03 | 2027-09-27 → 2028-01-23 |
| M11, M17 | AI in Project Control | `MOD-20260912134643669112` | C04 / G04 | 2027-06-09 → 2027-09-22 |
| M15 | PMI-SP Scheduling Professional | `MOD-20260912134748896463` | C04 / G04 | 2027-06-09 → 2027-09-22 |
| M11, M17 | AI in Project Control | `MOD-20261001110057418256848067C3F46B` | C04 / G05 | 2027-06-10 → 2027-09-29 |
| M12, M18 | Risk Management | `MOD-20260912133526796221` | C04 / G04 | 2027-09-27 → 2028-01-19 |
| M12, M18 | Risk Management | `MOD-20261001110237531696ACBE5C3BB08E` | C04 / G05 | 2027-10-07 → 2028-01-26 |
| M19 | PMI PMO | `MOD-20260912133050641690` | C04 / G04 | 2028-02-09 → 2028-05-24 |
| M13, M22 | PPC | `MOD-20260912132052479676` | C04 / G04 | 2028-02-10 → 2028-05-31 |
| M19 | PMI PMO | `MOD-2026100111080958797426535A71280F` | C04 / G05 | 2028-02-10 → 2028-05-31 |
| M13, M22 | PPC | `MOD-2026100111101527614080126D27D623` | C04 / G05 | 2028-02-10 → 2028-05-31 |
| M21 | MSP | `MOD-20260912134909006787` | C04 / G04 | 2028-06-06 → 2028-09-20 |
| M20, M27 | Portfolio Management | `MOD-20260912125821447490` | C04 / G04 | 2028-06-07 → 2028-09-20 |
| M14, M28 | EVM | `MOD-20260912113803682139` | C04 / G04 | 2028-06-08 → 2028-09-27 |
| M21 | MSP | `MOD-202610011055392208554E5AEEA80347` | C04 / G05 | 2028-06-08 → 2028-09-27 |
| M20, M27 | Portfolio Management | `MOD-202610011111188096444893DEB489B6` | C04 / G05 | 2028-06-08 → 2028-09-27 |
| M14, M28 | EVM | `MOD-202610011112472281824E5986C4E06B` | C04 / G05 | 2028-06-08 → 2028-09-27 |

## Records that must remain unavailable

APM PMP and AI in Project Management, PCP chartered Risk/Quality/Issue and AI in Project Management have no verified matches. Keep them unavailable until authoritative identity and deliveries are provided; do not substitute nearby subjects. The reader additionally returned these unrelated/test-like rows, all excluded from every candidate mapping:

| Programme | Exact title | Offering ID | Flag |
|---|---|---|---|
| PROG-20260907141619067932 | Definition of Project Management | `MOD-20260915085839492375AC51411A2A2A` | UNRELATED / NO VERIFIED ACADEMIC IDENTITY; excluded |
| PROG-20260907141619067932 | Ay Haga copy | `MOD-20260915111253407938EAEE0862C345` | TEST-LIKE; excluded |
| PROG-20260907141619067932 | Stakeholder Management &Reporting | `MOD-202610042359447369635E44A6808359` | UNRELATED / NO VERIFIED ACADEMIC IDENTITY; excluded |
| PROG-20260907141619067932 | Last Month: Project Manager | `MOD-2026091321325829443785F36D39BD63` | UNRELATED / NO VERIFIED ACADEMIC IDENTITY; excluded |
| PROG-PCP-L6 | Ay Haga | `MOD-20260915111150731637489690C06243` | TEST-LIKE; excluded |

Archived/deleted cohorts and invalid/deleted modules are already excluded by the existing reader. Prior audit exact excluded cohort IDs and overlapping reason counts are preserved in JSON with historical provenance; no deleted row was reintroduced. Two distinct PCP PMP G2 offering IDs share dates; neither is deduplicated or treated as an academic identity. Both already started and are retained only as historical evidence.

## Later UI rules — specification only

- ME/MM/APM: keep fixed academics. Join approved deliveries using exact programme + the learner's saved home cohort ID only; no cohort switching. Unknown/pending home cohort means no invented schedule.
- PCP: read only `modules_selection.modulesByPathway[modules_selection.pathway]` with the learner's saved module configuration; preserve all active-path mandatory modules and confirmed optional choices. Never union inactive pathways or count the Certified PMO parent heading as a module.
- Operational: PMP 2 + AI Control 1 + Risk APM 1, exactly two 1-credit optional choices, target 6. Strategic: same mandatory 4 credits, exactly two 1-credit optional choices, target 6. Chartered: four mandatory PMO components + AI Project Management, exactly one optional, target 6.
- PCP may choose real future offerings from other compatible PCP cohorts only after academic mapping/compatibility approval. Store selected delivery separately; never change the saved HOME cohort.
- New choices require start_date strictly greater than the current Europe/London calendar date, valid ordered finite dates and exact programme/cohort association. Keep G1/G2 delivery IDs distinct. Recheck reader validity and eligibility when saving; this dated proposal is not a runtime allowlist.
- Preserve already-saved past-start delivery selections and history; disable them for new selection. Deleted/archived records must not be offered anew or silently substituted, and invalid historical selections must not be erased automatically.
- Group master-table membership and unapproved academic equivalence remain unverified. Never expose a candidate to learners as a verified schedule until approved. No scheduling UI, persistence model, approval records or runtime integration is implemented here.

## Approval action

Reply using M01–M28: approve or reject each proposed exact title binding, supplying authoritative syllabus/qualification confirmation. For H rows, explicitly decide the distinctions stated in that row and which aliases are valid. A single shared delivery can satisfy two different academic IDs only with explicit approval for each path/requirement. For N rows, supply an authoritative exact-programme delivery or keep unavailable. No current mapping is formally approved.

Safety: only two new documentation files were added. No database writes, flags, publications, learner selections, academic definitions, Training Plan or Compliance Documents were changed.
