import { ddmmToIso, ageFromDob, WizardContext, useWizard, type WizardContextValue } from './WizardContext.helpers';
import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import {
  fetchExtendedIlr,
  peekExtendedIlr,
  saveExtendedIlr,
  type ExtendedIlrResponse,
  type LearnerKind,
} from '@/api/extendedIlr';
import { uploadEnrolmentDocument } from '@/api/enrolmentDocuments';
import { fetchKsbProfile } from '@/api/curriculum';
import { fetchWizardLayout, peekWizardLayout } from '@/api/wizardLayout';
import { fetchInclusionStatus } from '@/api/wizardInclusion';
import { ilrDocumentBlob, ilrDocumentFilename } from './steps/ilrDocument';
import { layoutForLearner, resolveLayout, visibleSteps } from './layout/resolve';
import { WizardTextsContext } from './layout/textsContext';
import type { CustomAnswer, LayoutStep, WizardLayout } from './layout/types';
import { WIZARD_STEPS, type EnrolmentBoard, type IlrForm, type Ksb, type WizardDraft } from '../types';

function emptyIlr(firstNames: string, surname: string): IlrForm {
  return {
    contact: { byPost: null, byPhone: null, byEmail: null },
    nextOfKin: { fullName: '', relationship: '', email: '', phone: '', sameAddressAsLearner: null, postcode: '', address: '' },
    eligibility: {
      employedInEngland: null, countryOfResidence: '', ukEeaNational: null, nationality: '',
      residentPrev3Years: null, yearsInUk: undefined, requiresWorkPermit: null, evidenceDescription: '', evidenceFiles: [],
    },
    employer: { organisationName: '', postcode: '', address: '', city: '', lineManagerName: '', lineManagerEmail: '', lineManagerPhone: '' },
    otherTraining: { attended12m: null, completedWhen: '' },
    circumstances: { caringResponsibilities: '', other: '', careLeaver: null },
    understanding: { programmeUnderstanding: '', careerProgression: '' },
    additionalInformation: { jobRoleRelevance: '', residenceNotForFullTimeEducation: '', ehcp: '', otherNames: '' },
    additional: { aged16to18: null, aged19to24: null },
    media: { consent: null },
    declarations: {
      plrShared: null, dfeContact: null, epaoDetails: null, kbcHoldsCerts: null, infoAccurate: null,
      over50PercentEngland: null, wageRateBand: '', knownByOtherName: null, plrAccessAware: null,
    },
    // Pre-fill the declaration name from the learner record; the signature and
    // date stay empty until someone actually signs.
    learnerSignature: { firstNames, surname, date: '' },
    providerSignature: { printName: '', date: '' },
  };
}

function makeInitialDraft(b: EnrolmentBoard): WizardDraft {
  const parts = b.user.name.split(' ');
  const first = parts[0] ?? '';
  const last = parts.slice(1).join(' ');
  const iso = ddmmToIso(b.contact.dob);
  return {
    // Age is derived from the DOB, so a learner whose record already carries one
    // arrives with the field filled — it is read-only and could not be answered
    // by hand otherwise.
    personalDetails: { firstName: first, lastName: last, email: b.contact.email, phone: b.contact.phone, address: '', dob: iso, age: ageFromDob(iso), sex: '' },
    // ILR Learner Details. State benefits start at 'None', the answer most
    // learners give, as on the paper form.
    ilrDetails: {
      yearsAtAddress: null, sinceBirth: null, postcodePriorToEnrolment: '', niNumber: '', niApplied: null,
      legalSex: '', pronouns: '', ethnicity: '', longTermDisability: null, highestQualification: '',
      employmentStatus: '', employmentStartDate: '', jobTitle: '', selfEmployed: null,
      fullTimeEducation: null, expectedLeavingDate: '', lengthOfUnemployment: '', volunteers: null,
      stateBenefits: 'None', benefitClaimBasis: '', signature: null, signatureDate: '',
    },
    // The standard is resolved from the learner's own programme by the Skills
    // Radar step (curriculum.ksb_profiles), so it isn't seeded to a fixed one.
    skillsRadar: { standardId: '', assessments: {} },
    ilr: emptyIlr(first, last),
    plr: { uln: '', records: [] },
    cvJob: {
      pmQualifications: '', experienceText: '', functionalSkillsEnrol: '',
      highestQualification: '', highestQualificationField: '', hasFieldQualification: null,
      highestFieldQualification: '', gcseEnglish: null, gcseMaths: null,
    },
    policies: { acknowledged: {} },
  };
}

/**
 * Fill blank Personal Details answers from what the learner's record already
 * holds — the phone, address, date of birth and signature a new apprentice
 * gives on their first sign-in, before this wizard opens.
 *
 * Blanks only: anything already answered in the wizard is kept. Applied after
 * the saved draft is merged, because that merge replaces personalDetails
 * wholesale and would otherwise drop the record's values again. Returns the
 * same object when there is nothing to fill, so an untouched draft still reads
 * as saved.
 */
function withRecordDefaults(d: WizardDraft, b: EnrolmentBoard): WizardDraft {
  const pd = d.personalDetails;
  const c = b.contact;
  const blank = (v?: string) => !v || v.trim() === '';
  const fill: Partial<WizardDraft['personalDetails']> = {};
  if (blank(pd.phone) && !blank(c.phone)) fill.phone = c.phone;
  if (blank(pd.address) && !blank(c.address)) fill.address = c.address;
  if (blank(pd.dob)) {
    const iso = ddmmToIso(c.dob);
    if (iso) {
      fill.dob = iso;
      fill.age = ageFromDob(iso);
    }
  }
  if (blank(pd.signature) && !blank(c.savedSignature)) {
    fill.signature = c.savedSignature;
    fill.signatureDate = c.savedSignatureDate || new Date().toISOString().slice(0, 10);
  }
  if (Object.keys(fill).length === 0) return d;
  return { ...d, personalDetails: { ...pd, ...fill } };
}

/**
 * Fold a saved ILR response onto a seeded draft.
 *
 * Extracted so the synchronous cache seed and the async hydration below cannot
 * drift: a returning learner must get exactly the same draft whether their
 * answers came from memory or from the wire.
 */
function mergeSaved(prev: WizardDraft, res: ExtendedIlrResponse): WizardDraft {
  const next: WizardDraft = { ...prev, ...(res.draft ?? {}) };
  // Merged per-section over the seeded blanks so a step added after this row was
  // saved still gets its defaults instead of coming back undefined.
  if (res.answers) next.ilr = { ...prev.ilr, ...res.answers };
  // Recomputed rather than trusted: a row saved last year carries an age that is
  // now a year out of date, and rows written before age was derived may hold a
  // hand-typed value that never matched the DOB.
  if (next.personalDetails?.dob) {
    next.personalDetails = { ...next.personalDetails, age: ageFromDob(next.personalDetails.dob) };
  }
  // The Inclusion status comes from the Safeguarding database on every open,
  // never from a saved draft, which the learner could have written themselves.
  next.inclusion = prev.inclusion;
  // Likewise the programme's KSB list: read from the curriculum, never saved.
  if (next.skillsRadar) next.skillsRadar = { ...next.skillsRadar, programmeKsbIds: prev.skillsRadar?.programmeKsbIds };
  return next;
}

export function WizardProvider({
  userId,
  isCommercial = false,
  board,
  readOnlyLearnerSteps = false,
  layout: layoutOverride,
  children,
}: {
  userId: string;
  isCommercial?: boolean;
  board: EnrolmentBoard;
  /** Set on the staff wizard: learner-owned steps render read-only. */
  readOnlyLearnerSteps?: boolean;
  /** Render this layout instead of the published one (the builder's preview). */
  layout?: WizardLayout;
  children: ReactNode;
}) {
  const kindForSeed: LearnerKind = isCommercial ? 'commercial' : 'apprenticeship';
  /**
   * The saved answers, if they are already in memory.
   *
   * The bootstrap primes this cache before the provider mounts, so on a normal
   * open — and on every revisit within the TTL — the wizard can hydrate on its
   * first frame. Read during render rather than in an effect precisely because
   * effects run after paint: hydrating there always cost one visible frame of
   * "Loading your answers…" for a draft that was never actually missing.
   */
  const seed = peekExtendedIlr(kindForSeed, userId);

  // `saved` is what the server holds; `filled` adds the record's values to its
  // blanks. Kept apart so the filled-in values count as unsaved (see
  // lastSavedDraft below) and are written on the next move, not only shown.
  const [initial] = useState(() => {
    const blank = makeInitialDraft(board);
    const saved = seed && (seed.answers || seed.draft) ? mergeSaved(blank, seed) : blank;
    // Apprentices only: the first-sign-in screens that capture these are theirs.
    return { saved, filled: isCommercial ? saved : withRecordDefaults(saved, board) };
  });
  const [draft, setDraft] = useState<WizardDraft>(initial.filled);
  // Read by the hydration effect, which is keyed on the learner rather than on
  // every new board object.
  const boardRef = useRef(board);
  boardRef.current = board;
  const [completed, setCompleted] = useState<boolean[]>(() => Array(WIZARD_STEPS.length).fill(false));
  const [ilrSaving, setIlrSaving] = useState(false);
  const [ilrSavedAt, setIlrSavedAt] = useState(() => seed?.meta.updatedAt ?? '');
  const [ilrFiling, setIlrFiling] = useState(false);
  // A cache hit means the answers are already in `draft`, so there is nothing to
  // wait for; the effect below still runs and is a no-op on the same payload.
  const [hydrated, setHydrated] = useState(Boolean(seed));
  /** Whether the programme's competencies have been seeded (or settled as none). */
  const [ksbsSettled, setKsbsSettled] = useState(false);

  // The published layout. Read from the cache on the first frame when it is
  // already there (as the saved answers are), otherwise the default layout
  // stands in until the request settles. A failed load keeps the default — the
  // wizard as it was before the builder existed — rather than blocking anyone.
  const [layoutPeek] = useState(() => (layoutOverride ? undefined : peekWizardLayout()));
  const [publishedLayout, setPublishedLayout] = useState<WizardLayout | null>(() => layoutPeek?.layout ?? null);
  const [layoutSettled, setLayoutSettled] = useState(Boolean(layoutOverride || layoutPeek));
  useEffect(() => {
    if (layoutOverride) return;
    let cancelled = false;
    fetchWizardLayout()
      .then((res) => { if (!cancelled) setPublishedLayout(res.layout ?? null); })
      .catch(() => {
        // Not fatal: the default layout stays in place.
      })
      .finally(() => { if (!cancelled) setLayoutSettled(true); });
    return () => { cancelled = true; };
  }, [layoutOverride]);
  const layout = useMemo(
    () => layoutForLearner(resolveLayout(layoutOverride ?? publishedLayout), isCommercial),
    [layoutOverride, publishedLayout, isCommercial]
  );
  const steps = useMemo(() => visibleSteps(layout), [layout]);

  /**
   * The draft as it was last written to the server (or last read from it).
   *
   * saveIlr writes the whole 8-step draft in one row, so without this every
   * step change fired a full write even when the learner had typed nothing —
   * a step with no inputs at all (Introduction, Next Steps) would still spin
   * "Saving…" on the way out of it. Reference equality is the whole test:
   * every edit goes through setSection, which always builds a new object, so
   * an untouched draft is still the very same object that was saved.
   */
  const lastSavedDraft = useRef<WizardDraft | null>(null);
  // A draft seeded from the cache came off the server, so it is already saved.
  // Without this the learner's first move fired a full write of answers that had
  // not changed — and showed the "Saving…" spinner while doing it.
  if (lastSavedDraft.current === null && seed && (seed.answers || seed.draft)) {
    lastSavedDraft.current = initial.saved;
  }

  const kind: LearnerKind = kindForSeed;

  // Hydrate the previously saved Extended ILR. A learner with nothing saved
  // comes back with answers: null, which leaves the seeded blank form in place.
  useEffect(() => {
    let cancelled = false;
    fetchExtendedIlr(kind, userId)
      .then((res) => {
        if (cancelled || (!res.answers && !res.draft)) return;
        setDraft((prev) => {
          const merged = mergeSaved(prev, res);
          // What just came back from the server is, by definition, saved — so
          // opening the wizard and paging through it writes nothing. Values
          // filled in from the learner's record are the exception: they are not
          // saved yet, so they leave the draft dirty and go out on the next move.
          lastSavedDraft.current = merged;
          return kind === 'commercial' ? merged : withRecordDefaults(merged, boardRef.current);
        });
        setIlrSavedAt(res.meta.updatedAt);
      })
      .catch(() => {
        // A failed load is not fatal — the wizard still opens on a blank form.
      })
      // Flagged in .finally, not .then: a failed load must still release the
      // gating, otherwise the learner is stuck on step one with no way forward.
      .finally(() => {
        if (!cancelled) setHydrated(true);
      });
    return () => {
      cancelled = true;
    };
  }, [kind, userId]);

  /**
   * Seed an unrated row for every competency on the learner's programme.
   *
   * The Skills Radar's completeness is measured against the assessments held in
   * the draft, and only the step itself knows the KSB list — so without this a
   * learner who never opened that step would have no rows at all and the step
   * would count as finished, letting them tab straight past their own
   * self-assessment. Seeded here rather than in the step because the gating
   * judges every step at once, including ones that have never been rendered.
   *
   * Runs after hydration (the merge above replaces skillsRadar wholesale) and
   * never for staff, who review this step rather than answer it.
   */
  const programmeName = board.programme.name || '';
  useEffect(() => {
    // Staff never seed, and a learner with no programme has nothing to seed —
    // both are settled by definition, or `ready` would never come true and the
    // rail would sit on "Loading your answers…" for good.
    if (readOnlyLearnerSteps || !programmeName) {
      setKsbsSettled(true);
      return;
    }
    // Still waiting on the saved answers; this effect re-runs when they land.
    if (!hydrated) return;
    let cancelled = false;
    fetchKsbProfile(programmeName)
      .then((res) => {
        const ksbs = res.results as Ksb[];
        if (cancelled) return;
        setDraft((prev) => {
          const assessments = { ...prev.skillsRadar.assessments };
          for (const k of ksbs) {
            if (assessments[k.id]) continue;
            assessments[k.id] = { ksbId: k.id, level: null, evidenceFiles: [], actionPlan: null };
          }
          // What the step asks, so rows for KSBs no longer on the programme —
          // which the learner never sees — cannot hold the step back. They are
          // kept, not deleted: they may be answers given under an earlier profile.
          const next = {
            ...prev,
            skillsRadar: { ...prev.skillsRadar, assessments, programmeKsbIds: ksbs.map((k) => k.id) },
          };
          // Seeding is not an answer: these rows are unrated placeholders that
          // are regenerated from the programme on every load, so they are not
          // worth a write of their own. Counting them as dirty would make the
          // first Next of every visit save — the spinner this fix removes.
          // A learner rating a competency dirties the draft normally.
          if (lastSavedDraft.current === prev) lastSavedDraft.current = next;
          return next;
        });
      })
      .catch(() => {
        // The step surfaces its own load error; a learner is not blocked on
        // competencies we could not fetch.
      })
      // Settled in .finally for the same reason `hydrated` is: a failed fetch
      // must still release whatever is waiting on it, or the rail would report
      // "loading" forever on a learner whose profile could not be read.
      .finally(() => {
        if (!cancelled) setKsbsSettled(true);
      });
    return () => {
      cancelled = true;
    };
  }, [readOnlyLearnerSteps, hydrated, programmeName]);

  /**
   * Whether the learner has answered the inclusion assessment, read from the
   * Safeguarding database by email (enrolment_api/wizard_inclusion.py) into
   * draft.inclusion. Read after hydration, like the competencies above, and
   * again on request. Like the seeding it is not an answer, so it never makes
   * the draft dirty; saveIlr leaves it out of what is written.
   */
  const [inclusionSettled, setInclusionSettled] = useState(false);
  const [inclusionChecking, setInclusionChecking] = useState(false);
  const [inclusionError, setInclusionError] = useState<string | null>(null);
  const [inclusionToken, setInclusionToken] = useState(0);
  useEffect(() => {
    if (!hydrated) return;
    let cancelled = false;
    setInclusionChecking(true);
    setInclusionError(null);
    fetchInclusionStatus(kind, userId)
      .then((status) => {
        if (cancelled) return;
        setDraft((prev) => {
          const next = { ...prev, inclusion: status };
          if (lastSavedDraft.current === prev) lastSavedDraft.current = next;
          return next;
        });
      })
      .catch((e: Error) => {
        // The step shows the failure and offers to check again; until a check
        // succeeds the assessment is not counted as done.
        if (!cancelled) setInclusionError(e.message);
      })
      // Settled in .finally for the same reason `hydrated` is.
      .finally(() => {
        if (!cancelled) {
          setInclusionChecking(false);
          setInclusionSettled(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [hydrated, kind, userId, inclusionToken]);
  const inclusionCheck = useMemo(
    () => ({ checking: inclusionChecking, error: inclusionError, refresh: () => setInclusionToken((n) => n + 1) }),
    [inclusionChecking, inclusionError]
  );

  const value = useMemo<WizardContextValue>(
    () => ({
      userId,
      isCommercial,
      board,
      draft,
      readOnly: readOnlyLearnerSteps,
      setSection: (key, val) => setDraft((prev) => ({ ...prev, [key]: val })),
      setCustom: (fieldKey, val) => setDraft((prev) => ({ ...prev, custom: { ...(prev.custom ?? {}), [fieldKey]: val } })),
      layout,
      steps,
      completed,
      markComplete: (index, done) => setCompleted((prev) => prev.map((c, i) => (i === index ? done : c))),
      saveIlr: async () => {
        // Nothing has changed since the last write — moving off a step the
        // learner didn't touch costs no request and shows no spinner.
        if (draft === lastSavedDraft.current) return;
        // Captured before the await: the learner can keep typing while the
        // request is in flight, and marking *that* newer draft saved would
        // silently drop the edits made during the round-trip.
        const saving = draft;
        setIlrSaving(true);
        try {
          // Save every step, not just the ILR — the other steps have no storage
          // of their own, so without this they would be lost on close.
          const { ilr, ...rest } = saving;
          // Read from the Safeguarding database on every open; never stored.
          delete rest.inclusion;
          // Read from the curriculum on every open; never stored.
          if (rest.skillsRadar) {
            const skillsRadar = { ...rest.skillsRadar };
            delete skillsRadar.programmeKsbIds;
            rest.skillsRadar = skillsRadar;
          }
          const res = await saveExtendedIlr(kind, userId, ilr, rest);
          lastSavedDraft.current = saving;
          setIlrSavedAt(res.meta.updatedAt);
        } finally {
          setIlrSaving(false);
        }
      },
      ilrSaving,
      ilrSavedAt,
      hydrated,
      ready: hydrated && ksbsSettled && layoutSettled && inclusionSettled,
      fileIlrDocument: async () => {
        setIlrFiling(true);
        try {
          const sig = draft.ilr.learnerSignature;
          // Printed in the wording the learner was asked — edits from the builder included.
          const pdf = await ilrDocumentBlob(draft.ilr, board, layout.texts);
          await uploadEnrolmentDocument(
            kind,
            userId,
            'extended-ilr',
            pdf,
            ilrDocumentFilename(board),
            // Flagged signed only when both parties have signed, matching how
            // the ILR row's Completed is derived server-side.
            { signed: Boolean(sig.signatureUrl && draft.ilr.providerSignature.signatureUrl), learnerName: board.user.name }
          );
        } finally {
          setIlrFiling(false);
        }
      },
      ilrFiling,
      inclusionCheck,
    }),
    [userId, isCommercial, board, readOnlyLearnerSteps, draft, layout, steps, completed, kind, ilrSaving, ilrSavedAt, hydrated, ksbsSettled, layoutSettled, ilrFiling, inclusionSettled, inclusionCheck]
  );

  return (
    <WizardContext.Provider value={value}>
      <WizardTextsContext.Provider value={layout.texts}>{children}</WizardTextsContext.Provider>
    </WizardContext.Provider>
  );
}
