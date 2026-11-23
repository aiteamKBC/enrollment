# ILR 2026-27 wording approval

Status: **Pending final Compliance approval before production rollout.**

The active seed contains the approved-reference five-paragraph Learning
Declaration, PLR explanation, EHCP explanation, Working in England clarification
and date-effective minimum-wage link requested for the 2026-27 ILR update.

Publishing or deploying this seed must remain a deliberate release action after
Compliance has approved the wording. Existing issued documents, signed
snapshots, stored template revisions and frozen PDF artefacts are not changed by
this source update.

The existing `careLeaver` question remains labelled "Care leaver" because the
stored answer's historical wording does not prove that it meant specifically
"in care on or after the 16th birthday". Compliance/product must decide whether
to introduce a separately versioned question; this change does not reinterpret
old answers.

## Additional EHCP disclosure wording

**Compliance approval is still required** for both active EHCP explanatory
paragraphs: the employer/funding disclosure paragraph before the EHCP question,
and the funding, reasonable-adjustment, evidence and confidentiality paragraph
after it. This implementation keeps the requested wording visible and versioned,
but does not treat it as legally approved or apply it to an issued snapshot.

## Retained legacy ILR fields

The active managed template still contains the two age-band answers, Media
Consent, PLR sharing, DfE contact, EPAO details, certificate retention and
information-accuracy declarations. They can resolve only when those keys already
exist in `Extended_ILR.Answers`; the current Wizard has no producer for them.
They have deliberately not been removed or defaulted in this change. Product and
Compliance must decide whether to reintroduce versioned questions, remove the
rows from a future template version, or define another authoritative producer.

| Active template field | Saved path in `Extended_ILR.Answers` | Current Wizard producer | Decision status |
| --- | --- | --- | --- |
| Aged 16 to 18 | `additional.aged16to18` | None | Product decision required |
| Aged 19 to 24 | `additional.aged19to24` | None | Product decision required |
| Media Consent | `media.consent` | None | Product/Compliance decision required |
| PLR information shared | `declarations.plrShared` | None; replaced by `plrAccessAware` with different wording | Compliance decision required |
| DfE contact acknowledgement | `declarations.dfeContact` | None | Compliance decision required |
| EPAO details disclosure | `declarations.epaoDetails` | None | Compliance decision required |
| Certificate retention | `declarations.kbcHoldsCerts` | None | Compliance decision required |
| Information accurate and true | `declarations.infoAccurate` | None | Compliance decision required |

The binding route itself remains valid: `derive_answers()` copies the saved
`Answers` JSON, `field_values()` resolves the registered path, and the
template/PDF renderer displays an explicit `true` or `false` without inventing a
default. Production audit data and learner-level values are deliberately not
included in this repository document.
