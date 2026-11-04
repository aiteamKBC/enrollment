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
import { type EnrolmentBoard, type IlrForm, type Ksb, type WizardDraft } from '../types';
import { useSuitability } from '@/features/programme-suitability/useSuitability';
import { useModulesConfiguration } from '@/features/programme-suitability/modules/useModulesConfiguration';
import { moduleSelectionComplete, selectionFingerprint, type ProgrammeModuleSelection, type ModulesState } from '@/features/programme-suitability/modules/configuration';
import { saveModuleSelection } from '@/features/programme-suitability/modules/api';
import { ModuleSaveQueue } from '@/features/programme-suitability/modules/moduleSaveQueue';
import { mergeFields, preserveEdits } from './prefill';
import { isPcpRadar, radarProfileProgramme, resolvePcpRadar } from '@/features/programme-suitability/skills-radar/allocation';
import { isMmRadar, mmRadarProfileProgramme, resolveMmRadar } from '@/features/programme-suitability/skills-radar/marketingManager';
import { isMeRadar, meRadarProfileProgramme, resolveMeRadar } from '@/features/programme-suitability/skills-radar/marketingExecutive';
import { isApmRadar, isApmStaffReview, apmRadarProfileProgramme, resolveApmRadar } from '@/features/programme-suitability/skills-radar/associateProjectManager';
import { useCatalogue } from '@/features/programme-suitability/skills-radar/catalogue/useCatalogue';
import { catalogueQuestions } from '@/features/programme-suitability/skills-radar/catalogue/runtime';

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

function makeInitialDraft(b: EnrolmentBoard, isCommercial = false): WizardDraft {
  const parts = b.user.name.split(' ');
  const first = parts[0] ?? '';
  const last = parts.slice(1).join(' ');
  const iso = ddmmToIso(b.contact.dob);
  return {
    // Age is derived from the DOB, so a learner whose record already carries one
    // arrives with the field filled — it is read-only and could not be answered
    // by hand otherwise.
    personalDetails: {
      firstName: first, lastName: last, email: b.contact.email, phone: b.contact.phone,
      address: isCommercial ? '' : b.contact.address ?? '',
      ...(!isCommercial ? {
        country: b.contact.country ?? '', currentPostcode: b.contact.currentPostcode ?? '',
        addressLine1: b.contact.addressLine1 ?? '', addressLine2: b.contact.addressLine2 ?? '',
        addressLine3: b.contact.addressLine3 ?? '', addressLine4: b.contact.addressLine4 ?? '',
      } : {}),
      dob: iso, age: ageFromDob(iso), sex: '',
      ...(!isCommercial && (b.contact.title || b.contact.titleRequired !== false) ? { title: b.contact.title || '' } : {}),
    },
    // ILR Learner Details. State benefits start at 'None', the answer most
    // learners give, as on the paper form.
    ilrDetails: {
      yearsAtAddress: null, sinceBirth: null, postcodePriorToEnrolment: '', niNumber: '', niApplied: null,
      legalSex: '', pronouns: '', ethnicity: '', longTermDisability: null,
      ...(!isCommercial ? { healthConditionCodes: [], primaryHealthConditionCode: '', additionalSupportDetails: '' } : {}),
      highestQualification: '',
      employmentStatus: '', employmentStartDate: '', jobTitle: '', selfEmployed: null,
      fullTimeEducation: null, ...(!isCommercial ? { workingHoursPerWeek: '' } : {}),
      expectedLeavingDate: '', lengthOfUnemployment: '', volunteers: null,
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
  // Linked defaults are merged beneath saved answers centrally. Filling blanks
  // here would undo deliberate clearing of an already saved answer.
  if (b.contact.eligibilityLinked) return d;
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
  if (pd.signature == null && !blank(c.savedSignature)) {
    fill.signature = c.savedSignature;
    // Keep an unknown historical signing date unknown; opening a page is not signing.
    fill.signatureDate = c.savedSignatureDate || undefined;
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
  const source = res.prefill ? mergeFields<WizardDraft>(prev, { ...res.prefill.draft, ilr: mergeFields(prev.ilr, res.prefill.answers) }) : prev;
  const next: WizardDraft = mergeFields<WizardDraft>(source, res.draft ?? {});
  // Merged per-section over the seeded blanks so a step added after this row was
  // saved still gets its defaults instead of coming back undefined.
  if (res.answers) next.ilr = mergeFields(source.ilr, res.answers);
  // Recomputed rather than trusted: a row saved last year carries an age that is
  // now a year out of date, and rows written before age was derived may hold a
  // hand-typed value that never matched the DOB.
  if (!res.meta.ilrFinalisedAt && !res.meta.ilrLegacyReadOnly && next.personalDetails?.dob) {
    next.personalDetails = { ...next.personalDetails, age: ageFromDob(next.personalDetails.dob) };
  }
  // The Inclusion status comes from the Safeguarding database on every open,
  // never from a saved draft, which the learner could have written themselves.
  next.inclusion = prev.inclusion;
  next.programmeSuitability = prev.programmeSuitability;
  next.programmeModulesCompletion = prev.programmeModulesCompletion;
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
    const blank = makeInitialDraft(board, isCommercial);
    const saved = seed && (seed.answers || seed.draft || seed.prefill) ? mergeSaved(blank, seed) : blank;
    // Apprentices only: the first-sign-in screens that capture these are theirs.
    return { saved, filled: isCommercial || seed?.meta.ilrFinalisedAt || seed?.meta.ilrLegacyReadOnly ? saved : withRecordDefaults(saved, board) };
  });
  const [draft, setDraft] = useState<WizardDraft>(initial.filled);
  const [savedModuleChoice, setSavedModuleChoice] = useState<ProgrammeModuleSelection | undefined>(initial.saved.programmeModules);
  const [moduleEditVersion, setModuleEditVersion] = useState(0);
  const [savedModuleEditVersion, setSavedModuleEditVersion] = useState(0);
  const moduleEditRef = useRef(0);
  const moduleSavedEditRef = useRef(0);
  const moduleRevisionRef = useRef(0);
  const moduleServerLoaded = useRef(false);
  const moduleWriter = useRef<(selection: ProgrammeModuleSelection) => Promise<void>>(async () => {});
  const [moduleSaveQueue] = useState(() => new ModuleSaveQueue((selection) => moduleWriter.current(selection), () => {}));
  useEffect(() => () => { moduleSaveQueue.flushOnLeave(); }, [moduleSaveQueue]);
  // Read by the hydration effect, which is keyed on the learner rather than on
  // every new board object.
  const boardRef = useRef(board);
  boardRef.current = board;
  const [completedBySlug, setCompletedBySlug] = useState<Record<string, boolean>>({});
  const saveQueue = useRef<Promise<void>>(Promise.resolve());
  const loadFailed = useRef(false);
  const [ilrSaving, setIlrSaving] = useState(false);
  const [ilrSavedAt, setIlrSavedAt] = useState(() => seed?.meta.updatedAt ?? '');
  const [ilrFinalisedAt, setIlrFinalisedAt] = useState(() => seed?.meta.ilrFinalisedAt ?? '');
  const [ilrLegacyReadOnly, setIlrLegacyReadOnly] = useState(() => seed?.meta.ilrLegacyReadOnly ?? false);
  const [ilrFiling, setIlrFiling] = useState(false);
  // A cache hit means the answers are already in `draft`, so there is nothing to
  // wait for; the effect below still runs and is a no-op on the same payload.
  const [hydrated, setHydrated] = useState(Boolean(seed));
  /** Whether the programme's competencies have been seeded (or settled as none). */
  const [ksbsSettled, setKsbsSettled] = useState(false);
  const [radarProfile, setRadarProfile] = useState<{ programme: string; ksbs: Ksb[] | null; error: string }>({ programme: '', ksbs: null, error: '' });

  // The published layout. Read from the cache on the first frame when it is
  // already there (as the saved answers are), otherwise the default layout
  // stands in until the request settles. A failed load keeps the default — the
  // wizard as it was before the builder existed — rather than blocking anyone.
  const [layoutPeek] = useState(() => (layoutOverride ? undefined : peekWizardLayout({ kind: kindForSeed, learnerId: userId })));
  const [publishedLayout, setPublishedLayout] = useState<WizardLayout | null>(() => layoutPeek?.layout ?? null);
  const [publishedVersion, setPublishedVersion] = useState<number | null>(() => layoutPeek?.version ?? null);
  const [publishedLayoutToken, setPublishedLayoutToken] = useState<string | null>(() => layoutPeek?.layoutToken ?? null);
  const [layoutSettled, setLayoutSettled] = useState(Boolean(layoutOverride || layoutPeek));
  useEffect(() => {
    if (layoutOverride) return;
    let cancelled = false;
    fetchWizardLayout({ kind: kindForSeed, learnerId: userId })
      .then((res) => { if (!cancelled) { setPublishedLayout(res.layout ?? null); setPublishedVersion(res.version); setPublishedLayoutToken(res.layoutToken ?? null); } })
      .catch(() => {
        // Not fatal: the default layout stays in place.
      })
      .finally(() => { if (!cancelled) setLayoutSettled(true); });
    return () => { cancelled = true; };
  }, [layoutOverride, kindForSeed, userId]);
  const layout = useMemo(
    () => layoutForLearner(resolveLayout(layoutOverride ?? publishedLayout), isCommercial),
    [layoutOverride, publishedLayout, isCommercial]
  );
  const steps = useMemo(() => visibleSteps(layout), [layout]);
  const completed = useMemo(() => steps.map(step => Boolean(completedBySlug[step.slug])), [steps, completedBySlug]);
  const suitability = useSuitability(kindForSeed, userId, Boolean(layout.programmeSuitability), publishedVersion,
    ['submitted', 'completed'].includes(String(board.programme?.onboardingStatus).toLowerCase()) || layout.programmeSuitability?.enabled === false);
  const modules = useModulesConfiguration(kindForSeed, userId, suitability.selectionConfirmed, publishedVersion);
  const apmStaffReview = isApmStaffReview(readOnlyLearnerSteps, board.programme.name || '', suitability.state);
  // Unconfirmed staff reviews cannot use the learner modules endpoint (which
  // requires confirmation and may pin a snapshot). Read published metadata only.
  const reviewModules = useMemo<ModulesState | null>(() => apmStaffReview && !suitability.loading && !suitability.error && layout.programmeModules ? {
    configuration: layout.programmeModules, configurationVersion: publishedVersion,
    selectedProgramme: 'APM_L4', savedSelection: null,
  } : null, [apmStaffReview, suitability.loading, suitability.error, layout.programmeModules, publishedVersion]);
  const radarModulesData = apmStaffReview ? reviewModules : modules.data;
  const radarModulesError = apmStaffReview
    ? suitability.error || (layoutSettled && !suitability.loading && !reviewModules ? 'Published APM modules are unavailable. Reload the wizard to retry.' : '')
    : modules.error;
  useEffect(() => {
    if (!modules.data) return;
    moduleServerLoaded.current = true;
    moduleRevisionRef.current = modules.data.selectionRevision ?? 0;
    // A focus refresh may acknowledge another tab; keep this tab's unsaved edits.
    if (moduleEditRef.current !== moduleSavedEditRef.current) return;
    const saved = modules.data.savedSelection ?? undefined;
    setSavedModuleChoice(saved);
    setDraft((previous) => selectionFingerprint(previous.programmeModules) === selectionFingerprint(saved)
      ? previous : { ...previous, programmeModules: saved });
  }, [modules.data]);
  const modulesRequired = !['submitted', 'completed'].includes(String(board.programme?.onboardingStatus).toLowerCase()) && suitability.selectionConfirmed && suitability.state?.selectedProgramme === 'PCP_L6';
  const modulesComplete = Boolean(modules.data && modules.data.selectionComplete && !modules.error && savedModuleChoice && moduleEditVersion === savedModuleEditVersion &&
    selectionFingerprint(modules.data.savedSelection ?? undefined) === selectionFingerprint(savedModuleChoice) &&
    selectionFingerprint(draft.programmeModules) === selectionFingerprint(savedModuleChoice) &&
    moduleSelectionComplete(modules.data.configuration, savedModuleChoice, modules.data.configurationVersion));

  /**
   * The draft as it was last written to the server (or last read from it).
   *
   * saveIlr writes the other wizard answers in one row, so without this every
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
  const currentDraft = useRef(draft);
  currentDraft.current = draft;
  const originalSignature = useRef(initial.filled.personalDetails.signature);
  const originalSignatureDeclaration = useRef(initial.filled.personalDetails.signatureDeclaration);
  useEffect(() => {
    if (isCommercial) return;
    const preventSignatureLoss = (event: BeforeUnloadEvent) => {
      const saved = lastSavedDraft.current ? lastSavedDraft.current.personalDetails?.signature : originalSignature.current;
      const savedDeclaration = lastSavedDraft.current ? lastSavedDraft.current.personalDetails?.signatureDeclaration : originalSignatureDeclaration.current;
      if (currentDraft.current.personalDetails.signature !== saved
        || JSON.stringify(currentDraft.current.personalDetails.signatureDeclaration) !== JSON.stringify(savedDeclaration)) {
        event.preventDefault();
        event.returnValue = '';
      }
    };
    window.addEventListener('beforeunload', preventSignatureLoss);
    return () => window.removeEventListener('beforeunload', preventSignatureLoss);
  }, [isCommercial]);

  const kind: LearnerKind = kindForSeed;

  // Hydrate the previously saved Extended ILR. A learner with nothing saved
  // comes back with answers: null, which leaves the seeded blank form in place.
  useEffect(() => {
    let cancelled = false;
    fetchExtendedIlr(kind, userId)
      .then((res) => {
        loadFailed.current = false;
        if (cancelled || (!res.answers && !res.draft && !res.prefill)) return;
        setDraft((prev) => {
          const server = mergeSaved(initial.filled, res);
          const merged = preserveEdits(initial.filled, prev, server);
          if (moduleServerLoaded.current) merged.programmeModules = prev.programmeModules;
          // What just came back from the server is, by definition, saved — so
          // opening the wizard and paging through it writes nothing. Values
          // filled in from the learner's record are the exception: they are not
          // saved yet, so they leave the draft dirty and go out on the next move.
          // Early edits have not been saved. Source-only defaults are written
          // on the next normal save, never as a side effect of this read.
          lastSavedDraft.current = res.answers || res.draft ? server : null;
          return kind === 'commercial' || res.meta.ilrFinalisedAt || res.meta.ilrLegacyReadOnly ? merged : withRecordDefaults(merged, boardRef.current);
        });
        setIlrSavedAt(res.meta.updatedAt);
        setIlrFinalisedAt(res.meta.ilrFinalisedAt ?? '');
        setIlrLegacyReadOnly(res.meta.ilrLegacyReadOnly ?? false);
      })
      .catch(() => {
        // Never overwrite a signed historical draft with blanks after a failed read.
        loadFailed.current = true;
      })
      // Flagged in .finally, not .then: a failed load must still release the
      // gating, otherwise the learner is stuck on step one with no way forward.
      .finally(() => {
        if (!cancelled) setHydrated(true);
      });
    return () => {
      cancelled = true;
    };
  }, [kind, userId, initial.filled]);

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
  const apmRadar = isApmRadar(suitability.state) || apmStaffReview;
  const meRadar = isMeRadar(suitability.state);
  const mmRadar = isMmRadar(suitability.state);
  const programmeName = apmRadar ? apmRadarProfileProgramme(suitability.state) : meRadar ? meRadarProfileProgramme(suitability.state) : mmRadar ? mmRadarProfileProgramme(suitability.state) : radarProfileProgramme(board.programme.name || '', suitability.state);
  const pcpRadar = isPcpRadar(suitability.state);
  const moduleRadar = pcpRadar || mmRadar || meRadar || apmRadar;
  const catalogueModules = useMemo<ModulesState | null>(() => radarModulesData || (layout.programmeModules ? {
    configuration: layout.programmeModules, configurationVersion: publishedVersion,
    selectedProgramme: suitability.state?.selectedProgramme || 'APM_L4', savedSelection: null,
  } : null), [radarModulesData, layout.programmeModules, publishedVersion, suitability.state?.selectedProgramme]);
  const radarCatalogue = useCatalogue(Boolean(layout.skillsRadarCatalogues) && hydrated, kind, userId, programmeName,
    catalogueModules, radarModulesError, !layoutSettled || (Boolean(layout.skillsRadarCatalogues) && !hydrated));
  const managedRadar = radarCatalogue.enabled && !(radarCatalogue.data && !radarCatalogue.data.catalogue);
  const radarGrouping = useMemo(() => managedRadar ? radarCatalogue.grouping : pcpRadar ? resolvePcpRadar(radarProfile.programme === programmeName ? radarProfile.ksbs : null,
    modules.data, radarProfile.programme === programmeName ? radarProfile.error || modules.error : modules.error) : mmRadar ? resolveMmRadar(radarProfile.programme === programmeName ? radarProfile.ksbs : null,
    modules.data, radarProfile.programme === programmeName ? radarProfile.error || modules.error : modules.error) : meRadar ? resolveMeRadar(radarProfile.programme === programmeName ? radarProfile.ksbs : null,
    modules.data, radarProfile.programme === programmeName ? radarProfile.error || modules.error : modules.error) : apmRadar ? resolveApmRadar(radarProfile.programme === programmeName ? radarProfile.ksbs : null,
    radarModulesData, radarProfile.programme === programmeName ? radarProfile.error || radarModulesError : radarModulesError) : null,
    [managedRadar, radarCatalogue.grouping, pcpRadar, mmRadar, meRadar, apmRadar, radarProfile, programmeName, modules.data, modules.error, radarModulesData, radarModulesError]);
  useEffect(() => {
    if (!managedRadar || radarCatalogue.loading) return;
    const catalogue = radarCatalogue.data?.catalogue;
    if (catalogue) {
      const questions = catalogueQuestions(catalogue);
      setDraft((prev) => {
        const assessments = { ...prev.skillsRadar.assessments };
        if (!readOnlyLearnerSteps) for (const k of questions) {
          assessments[k.id] ??= { ksbId: k.id, level: null, evidenceFiles: [], actionPlan: null };
        }
        const next = { ...prev, skillsRadar: { ...prev.skillsRadar, assessments,
          programmeKsbIds: questions.map((q) => q.id), catalogueToken: radarCatalogue.data!.token } };
        if (lastSavedDraft.current === prev) lastSavedDraft.current = next;
        return next;
      });
    }
    setKsbsSettled(true);
  }, [managedRadar, radarCatalogue.loading, radarCatalogue.data, readOnlyLearnerSteps]);
  useEffect(() => {
    if (managedRadar || !layoutSettled) return;
    // Staff never seed, and a learner with no programme has nothing to seed —
    // both are settled by definition, or `ready` would never come true and the
    // rail would sit on "Loading your answers…" for good.
    if ((readOnlyLearnerSteps && !moduleRadar) || !programmeName) {
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
        setRadarProfile({ programme: programmeName, ksbs, error: '' });
        if (readOnlyLearnerSteps) return;
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
      .catch((error: unknown) => {
        if (!cancelled) setRadarProfile({ programme: programmeName, ksbs: null, error: error instanceof Error ? error.message : 'Could not load Skills Radar questions.' });
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
  }, [managedRadar, layoutSettled, readOnlyLearnerSteps, hydrated, programmeName, moduleRadar]);

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
      draft: { ...draft, ...(suitability.enabled ? { programmeSuitability: { required: suitability.required, status: suitability.status, selectionConfirmed: suitability.selectionConfirmed, selectedProgramme: suitability.state?.selectedProgramme },
        programmeModulesCompletion: { required: modulesRequired, complete: modulesComplete } } : {}),
        skillsRadar: radarGrouping ? { ...draft.skillsRadar, configurationError: radarGrouping.status === 'ready' ? undefined : radarGrouping.message } : draft.skillsRadar,
      },
      radarCatalogue,
      suitability,
      modules: apmStaffReview ? { ...modules, data: radarModulesData, error: radarModulesError } : modules,
      moduleSaveQueue,
      readOnly: readOnlyLearnerSteps,
      setSection: (key, val) => {
        if ((ilrFinalisedAt || ilrLegacyReadOnly) && !isCommercial && !readOnlyLearnerSteps && (key === 'personalDetails' || key === 'ilrDetails')) return;
        if (key === 'programmeModules') { moduleEditRef.current += 1; setModuleEditVersion(moduleEditRef.current); }
        setDraft((prev) => ({ ...prev, [key]: val }));
      },
      setCustom: (fieldKey, val) => setDraft((prev) => ({ ...prev, custom: { ...(prev.custom ?? {}), [fieldKey]: val } })),
      layout,
      steps,
      completed,
      markComplete: (index, done) => {
        const step = steps[index];
        if (step) setCompletedBySlug(previous => ({ ...previous, [step.slug]: done }));
      },
      saveModules: async (selection) => {
        if (readOnlyLearnerSteps || !hydrated || !modules.data) throw new Error('Module settings are still loading. Please retry.');
        const editing = moduleEditRef.current;
        const committed = await saveModuleSelection(kind, userId, selection, moduleRevisionRef.current);
        moduleRevisionRef.current = Math.max(moduleRevisionRef.current, committed.selectionRevision);
        moduleSavedEditRef.current = editing;
        setSavedModuleChoice(committed.selection);
        setSavedModuleEditVersion(editing);
        modules.acceptSaved(committed);
        // Canonical mandatory IDs must not replace a newer local selection.
        setDraft((previous) => selectionFingerprint(previous.programmeModules) === selectionFingerprint(selection)
          ? { ...previous, programmeModules: committed.selection } : previous);
      },
      saveIlr: async (options = {}) => {
        if (!hydrated || loadFailed.current) throw new Error('Your saved answers could not be loaded. Refresh and try again before saving.');
        const saving = draft;
        const operation = saveQueue.current.catch(() => {}).then(async () => {
          // Nothing has changed since the last write — moving off a step the
          // learner didn't touch costs no request and shows no spinner.
          const mustRecordVersion = Boolean(options.skillsRadarCheckpoint || options.submitted);
          if (!options.finalizeIlr && !mustRecordVersion && lastSavedDraft.current && Object.keys(saving).every((key) =>
            key === 'programmeModules' || key === 'programmeModulesCompletion' || saving[key as keyof WizardDraft] === lastSavedDraft.current?.[key as keyof WizardDraft])) return;
          // Captured before the await: the learner can keep typing while the
          // request is in flight, and marking *that* newer draft saved would
          // silently drop the edits made during the round-trip.
          setIlrSaving(true);
          try {
            // Modules use their own endpoint; the remaining answers keep the
            // existing wizard persistence and projection workflow.
            const { ilr, ...savedRest } = saving;
            const rest: Partial<typeof savedRest> = { ...savedRest };
            // Read from the Safeguarding database on every open; never stored.
            delete rest.inclusion;
            delete rest.programmeSuitability;
            delete rest.programmeModulesCompletion;
            delete rest.programmeModules;
            if (ilrLegacyReadOnly && !isCommercial) {
              delete rest.personalDetails;
              delete rest.ilrDetails;
            }
            // Read from the curriculum on every open; never stored.
            if (rest.skillsRadar) {
              const skillsRadar = { ...rest.skillsRadar };
              if (managedRadar && radarCatalogue.data?.catalogue) skillsRadar.catalogueToken = radarCatalogue.data.token;
              delete skillsRadar.programmeKsbIds;
              delete skillsRadar.configurationError;
              rest.skillsRadar = skillsRadar;
            }
            const res = await saveExtendedIlr(kind, userId, ilr, rest, {
              layoutVersion: publishedVersion,
              layoutToken: publishedLayoutToken,
              skillsRadarCheckpoint: Boolean(options.skillsRadarCheckpoint),
              submitted: Boolean(options.submitted),
              finalizeIlr: Boolean(options.finalizeIlr),
            });
            lastSavedDraft.current = saving;
            setIlrSavedAt(res.meta.updatedAt);
            setIlrFinalisedAt(res.meta.ilrFinalisedAt ?? '');
            setIlrLegacyReadOnly(res.meta.ilrLegacyReadOnly ?? false);
          } finally {
            setIlrSaving(false);
          }
        });
        saveQueue.current = operation;
        return operation;
      },
      ilrSaving,
      ilrSavedAt,
      ilrFinalisedAt,
      ilrLegacyReadOnly,
      hydrated,
      ready: hydrated && ksbsSettled && !radarCatalogue.loading && layoutSettled && inclusionSettled && (!suitability.enabled || !suitability.loading) && (!modulesRequired || Boolean(modules.data || modules.error)),
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
    [userId, isCommercial, board, readOnlyLearnerSteps, draft, layout, publishedVersion, publishedLayoutToken, steps, completed, kind, ilrSaving, ilrSavedAt, ilrFinalisedAt, ilrLegacyReadOnly, hydrated, ksbsSettled, layoutSettled, ilrFiling, inclusionSettled, inclusionCheck, suitability, modules, modulesRequired, modulesComplete, moduleSaveQueue, radarGrouping, radarCatalogue, managedRadar, apmStaffReview, radarModulesData, radarModulesError]
  );
  moduleWriter.current = value.saveModules;

  return (
    <WizardContext.Provider value={value}>
      <WizardTextsContext.Provider value={layout.texts}>{children}</WizardTextsContext.Provider>
    </WizardContext.Provider>
  );
}
