import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { WorkspaceShell } from '@/components/feature/WorkspaceShell';
import { AppIcon } from '@/components/feature/AppIcon';
import { AdminPageHeader } from '@/pages/admin/_shared/AdminPage';
import { useToast } from "@/hooks/useToast.helpers";
import { roleNavMap } from '@/mocks/navigation';
import { fetchEligibilityLayout, publishEligibilityLayout } from '@/api/eligibilityFormLayout';
import {
  BUILTIN_ITEMS,
  ITEM_TEXT_SLOTS,
  PAGE_TEXT_SLOTS,
  builtinSection,
  isBuiltinQuestion,
  textFor,
} from '@/pages/apprenticeship-application/layout/registry';
import { builtinOptions, isLocked, itemLabel, resolveLayout, sectionDescription } from '@/pages/apprenticeship-application/layout/resolve';
import { QUESTION_TYPES, type EligibilityItem, type EligibilityLayout, type EligibilitySection } from '@/pages/apprenticeship-application/layout/types';
import { btnPrimary, btnSecondary, iconBtn, inputClass } from '../components/ui';
import { TextEditor } from '../wizard-builder/TextEditor';
import { OptionsEditor } from './OptionsEditor';
import { QuestionEditor } from './QuestionEditor';
import {
  addSection,
  dependents,
  findItem,
  moveItem,
  moveItemToSection,
  moveSection,
  pendingColumns,
  problems,
  removeItem,
  saveCustomQuestion,
  setText,
  updateItem,
  updateSection,
  type QuestionDraft,
} from './builderOps';

const enrolmentNav = roleNavMap.apprentice;

/** The left-hand entry for the page heading, ahead of the sections. */
const PAGE = '__page';

const TYPE_LABEL: Record<string, string> = Object.fromEntries(QUESTION_TYPES.map((t) => [t.value, t.label]));

const clone = (layout: EligibilityLayout): EligibilityLayout => JSON.parse(JSON.stringify(layout)) as EligibilityLayout;

/**
 * The "Edit eligibility form" builder, for the public form at
 * /apprenticeship_application.
 *
 * Edits a copy of the published layout and publishes it in one go. Publishing
 * is what applicants see from then on, and is where the server adds a column
 * to enrolment.eligibility_form for each new question. Nothing is ever
 * dropped: removing a question hides it and keeps its column and answers.
 */
export default function EligibilityBuilderPage() {
  const navigate = useNavigate();
  const { success, error } = useToast();
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [version, setVersion] = useState<number | null>(null);
  const [updatedAt, setUpdatedAt] = useState('');
  const [updatedBy, setUpdatedBy] = useState('');
  const [published, setPublished] = useState<EligibilityLayout | null>(null);
  const [layout, setLayout] = useState<EligibilityLayout | null>(null);
  const [selected, setSelected] = useState<string>('');
  const [editing, setEditing] = useState<string | 'new' | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    fetchEligibilityLayout()
      .then((res) => {
        if (cancelled) return;
        const resolved = resolveLayout(res.layout);
        setPublished(resolved);
        setLayout(clone(resolved));
        setVersion(res.version);
        setUpdatedAt(res.updatedAt ?? '');
        setUpdatedBy(res.updatedBy ?? '');
        setSelected((cur) => (cur === PAGE || resolved.sections.some((s) => s.slug === cur) ? cur : resolved.sections[0]?.slug ?? PAGE));
      })
      .catch((e: Error) => { if (!cancelled) setLoadError(e.message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [reloadToken]);

  const dirty = useMemo(() => Boolean(layout && published && JSON.stringify(layout) !== JSON.stringify(published)), [layout, published]);

  // Unpublished edits are easy to lose by navigating away; ask first.
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ''; };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);

  const leave = () => {
    if (dirty && !window.confirm('You have unpublished changes to the eligibility form. Leave without publishing them?')) return;
    navigate('/users');
  };

  const edit = useCallback((fn: (l: EligibilityLayout) => EligibilityLayout) => setLayout((l) => (l ? fn(l) : l)), []);

  const publish = async () => {
    if (!layout) return;
    setPublishing(true);
    try {
      const saved = await publishEligibilityLayout(layout, version);
      const resolved = resolveLayout(saved.layout);
      setPublished(resolved);
      setLayout(clone(resolved));
      setVersion(saved.version);
      setUpdatedAt(saved.updatedAt ?? '');
      setUpdatedBy(saved.updatedBy ?? '');
      // A new section's temporary slug is replaced by the server's.
      setSelected((cur) => (cur === PAGE || resolved.sections.some((s) => s.slug === cur) ? cur : resolved.sections[resolved.sections.length - 1]?.slug ?? PAGE));
      setConfirming(false);
      success('Eligibility form published', 'Applicants see the changes the next time they open the form.');
    } catch (e) {
      error('Could not publish the eligibility form', e instanceof Error ? e.message : 'Unexpected error');
    } finally {
      setPublishing(false);
    }
  };

  const section = layout?.sections.find((s) => s.slug === selected) ?? null;

  return (
    <WorkspaceShell role="compliance" roleLabel={enrolmentNav.label} navItems={enrolmentNav.items} workspaceLabel={enrolmentNav.workspaceLabel} pageTitle="Eligibility form" pageSubtitle="Edit the public apprenticeship eligibility form">
      <div className="admin-console-page min-w-0 space-y-4 p-3 md:space-y-5 md:p-6">
        <AdminPageHeader
          title="Apprenticeship eligibility form"
          description="Add, remove, reorder and require the questions applicants answer on the public eligibility form, and edit their wording and answer lists. Published changes apply to everyone who opens the form afterwards."
          icon="ri-task-line"
          eyebrow="Enrolment"
          actions={
            <div className="flex flex-wrap items-start gap-2">
              <button type="button" className={btnSecondary} onClick={leave}>
                <AppIcon className="ri-arrow-left-line" />Back to users
              </button>
              <Link className={btnSecondary} to="/apprenticeship_application" target="_blank" rel="noopener noreferrer">
                <AppIcon className="ri-external-link-line" />Open the form
              </Link>
              <button type="button" className={btnSecondary} disabled={!dirty || publishing} onClick={() => published && setLayout(clone(published))}>
                <AppIcon className="ri-arrow-go-back-line" />Discard changes
              </button>
              <button type="button" className={btnPrimary} disabled={!dirty || publishing} onClick={() => setConfirming(true)}>
                <AppIcon className="ri-upload-cloud-2-line" />Publish changes
              </button>
            </div>
          }
        />

        {loading && <p className="text-[13px] text-foreground-400"><AppIcon className="ri-loader-4-line mr-2 inline animate-spin" />Loading the eligibility form…</p>}
        {!loading && loadError && (
          <div className="rounded-2xl border border-red-200 bg-red-50 p-4 text-[13px] text-red-700">
            <AppIcon className="ri-error-warning-line mr-1.5 inline" />{loadError}
            <button type="button" className={`${btnSecondary} ml-3`} onClick={() => setReloadToken((n) => n + 1)}><AppIcon className="ri-refresh-line" />Retry</button>
          </div>
        )}

        {!loading && !loadError && layout && (
          <>
            <p className="text-[12px] text-foreground-500" aria-live="polite">
              {dirty ? (
                <span className="font-medium text-amber-700"><AppIcon className="ri-edit-circle-line mr-1 inline" />You have unpublished changes.</span>
              ) : version ? (
                <>Published version {version}{updatedAt ? ` · ${new Date(updatedAt).toLocaleString('en-GB')}` : ''}{updatedBy ? ` · ${updatedBy}` : ''}</>
              ) : (
                'Showing the standard form — nothing has been published from the builder yet.'
              )}
            </p>
            <div className="flex flex-col gap-4 lg:flex-row lg:items-start">
              <SectionList
                layout={layout}
                selected={selected}
                onSelect={(slug) => { setSelected(slug); setEditing(null); }}
                onMove={(slug, delta) => edit((l) => moveSection(l, slug, delta))}
                onAdd={() => {
                  const res = addSection(layout, 'New section');
                  setLayout(res.layout);
                  setSelected(res.slug);
                  setEditing(null);
                }}
              />
              {selected === PAGE ? (
                <PageEditor layout={layout} edit={edit} />
              ) : (
                section && (
                  <SectionEditor
                    key={section.slug}
                    layout={layout}
                    section={section}
                    editing={editing}
                    setEditing={setEditing}
                    edit={edit}
                  />
                )
              )}
            </div>
          </>
        )}

        {confirming && layout && (
          <PublishDialog layout={layout} publishing={publishing} onCancel={() => setConfirming(false)} onConfirm={publish} />
        )}
      </div>
    </WorkspaceShell>
  );
}

function SectionList({
  layout,
  selected,
  onSelect,
  onMove,
  onAdd,
}: {
  layout: EligibilityLayout;
  selected: string;
  onSelect: (slug: string) => void;
  onMove: (slug: string, delta: -1 | 1) => void;
  onAdd: () => void;
}) {
  let number = 0;
  const pageActive = selected === PAGE;
  return (
    <nav aria-label="Form sections" className="shrink-0 overflow-hidden rounded-2xl border border-foreground-200/60 bg-background-50 shadow-sm lg:w-72">
      <div className="border-b border-foreground-100 px-4 py-3">
        <p className="text-[11px] font-semibold uppercase tracking-wider text-foreground-400">Sections</p>
      </div>
      <div className="border-b border-foreground-100 p-2">
        <button
          type="button"
          onClick={() => onSelect(PAGE)}
          aria-current={pageActive ? 'step' : undefined}
          className={`flex w-full cursor-pointer items-center gap-2.5 rounded-xl px-2.5 py-2 text-left ${pageActive ? 'bg-primary-50 text-primary-700' : 'text-foreground-700 hover:bg-background-100'}`}
        >
          <span className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-[11px] ${pageActive ? 'border-primary-300 bg-primary-100' : 'border-foreground-200'}`}>
            <AppIcon className="ri-layout-top-line text-[11px]" />
          </span>
          <span className="min-w-0">
            <span className={`block truncate text-[12.5px] ${pageActive ? 'font-semibold' : 'font-medium'}`}>Form heading</span>
            <span className="block text-[10.5px] text-foreground-400">Title and introduction</span>
          </span>
        </button>
      </div>
      <ol className="space-y-0.5 p-2">
        {layout.sections.map((s, i) => {
          if (!s.hidden) number += 1;
          const active = s.slug === selected;
          return (
            <li key={s.slug} className={`flex items-center gap-1 rounded-xl ${active ? 'bg-primary-50' : 'hover:bg-background-100'}`}>
              <button
                type="button"
                onClick={() => onSelect(s.slug)}
                aria-current={active ? 'step' : undefined}
                aria-label={s.hidden ? `${s.title} (hidden)` : s.title}
                className={`flex min-w-0 flex-1 cursor-pointer items-center gap-2.5 px-2.5 py-2 text-left ${s.hidden ? 'text-foreground-300' : active ? 'text-primary-700' : 'text-foreground-700'}`}
              >
                <span className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-[11px] font-semibold ${active ? 'border-primary-300 bg-primary-100' : 'border-foreground-200'}`}>
                  {s.hidden ? <AppIcon className="ri-eye-off-line text-[11px]" /> : number}
                </span>
                <span className="min-w-0">
                  <span className={`block truncate text-[12.5px] ${active ? 'font-semibold' : 'font-medium'}`}>{s.title || 'Untitled section'}</span>
                  <span className="block text-[10.5px] text-foreground-400">{s.hidden ? 'Hidden' : s.builtin ? 'Standard section' : 'Added section'}</span>
                </span>
              </button>
              <button type="button" className={iconBtn} disabled={i === 0} aria-label={`Move ${s.title} up`} onClick={() => onMove(s.slug, -1)}>
                <AppIcon className="ri-arrow-up-s-line" />
              </button>
              <button type="button" className={iconBtn} disabled={i === layout.sections.length - 1} aria-label={`Move ${s.title} down`} onClick={() => onMove(s.slug, 1)}>
                <AppIcon className="ri-arrow-down-s-line" />
              </button>
            </li>
          );
        })}
      </ol>
      <div className="border-t border-foreground-100 p-2">
        <button type="button" onClick={onAdd} className="flex w-full cursor-pointer items-center justify-center gap-1.5 rounded-xl border border-dashed border-foreground-200 px-3 py-2 text-[12px] font-medium text-primary-700 hover:bg-primary-50">
          <AppIcon className="ri-add-line" />Add section
        </button>
      </div>
    </nav>
  );
}

function PageEditor({ layout, edit }: { layout: EligibilityLayout; edit: (fn: (l: EligibilityLayout) => EligibilityLayout) => void }) {
  return (
    <section aria-label="Form heading" className="min-w-0 flex-1 space-y-4 overflow-hidden rounded-2xl border border-foreground-200/60 bg-background-50 p-5 shadow-sm">
      <p className="text-[12px] text-foreground-500">The purple banner at the top of the form.</p>
      {PAGE_TEXT_SLOTS.map((slot) => (
        <TextEditor key={slot.key} slot={slot} texts={layout.texts} onChange={(v) => edit((l) => setText(l, slot.key, v))} />
      ))}
    </section>
  );
}

function SectionEditor({
  layout,
  section,
  editing,
  setEditing,
  edit,
}: {
  layout: EligibilityLayout;
  section: EligibilitySection;
  editing: string | 'new' | null;
  setEditing: (v: string | 'new' | null) => void;
  edit: (fn: (l: EligibilityLayout) => EligibilityLayout) => void;
}) {
  const shown = section.items.filter((it) => !it.hidden);
  const removed = section.items.filter((it) => it.hidden);
  const [showRemoved, setShowRemoved] = useState(false);
  const addRef = useRef<HTMLButtonElement>(null);
  // Hiding this section would hide a question that identifies the applicant.
  const holdsLocked = shown.some(isLocked);

  const saveQuestion = (draft: QuestionDraft) => {
    if (editing) {
      const key = editing === 'new' ? null : editing;
      edit((l) => saveCustomQuestion(l, section.slug, key, draft).layout);
    }
    setEditing(null);
    addRef.current?.focus();
  };

  return (
    <section aria-label={`${section.title || 'Untitled'} section`} className="min-w-0 flex-1 overflow-hidden rounded-2xl border border-foreground-200/60 bg-background-50 shadow-sm">
      <div className="grid gap-3 border-b border-foreground-100 px-5 py-4 sm:grid-cols-[1fr_auto] sm:items-end">
        <label className="min-w-[220px] text-[12px] font-medium text-foreground-600" htmlFor="eligibility-section-name">
          Section name
          <input id="eligibility-section-name" className={`${inputClass} mt-1`} value={section.title} onChange={(e) => edit((l) => updateSection(l, section.slug, { title: e.target.value }))} />
        </label>
        <label className={`flex items-center gap-2 pb-2 text-[12px] font-medium ${holdsLocked ? 'text-foreground-400' : 'text-foreground-700'}`}>
          <input
            type="checkbox"
            className="accent-primary-500"
            checked={!section.hidden}
            disabled={holdsLocked && !section.hidden}
            onChange={(e) => edit((l) => updateSection(l, section.slug, { hidden: !e.target.checked }))}
          />
          Show this section to applicants
        </label>
        <label className="text-[12px] font-medium text-foreground-600 sm:col-span-2" htmlFor="eligibility-section-description">
          Description
          <input
            id="eligibility-section-description"
            className={`${inputClass} mt-1`}
            value={sectionDescription(section)}
            onChange={(e) => {
              // Typing the standard description back is no edit.
              const standard = builtinSection(section.slug)?.description;
              edit((l) => updateSection(l, section.slug, { description: e.target.value === standard ? undefined : e.target.value }));
            }}
          />
        </label>
        {holdsLocked && <p className="text-[11px] text-foreground-400 sm:col-span-2"><AppIcon className="ri-lock-line mr-1 inline" />This section asks the questions that identify the applicant, so it is always shown.</p>}
      </div>
      {section.hidden && (
        <p className="mx-5 mt-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[12px] text-amber-800">
          <AppIcon className="ri-eye-off-line mr-1 inline" />This section is hidden. Applicants skip it, and none of its questions are asked. Answers already given are kept.
        </p>
      )}
      {!section.title.trim() && <p role="alert" className="mx-5 mt-3 text-[12px] text-red-600">Give the section a name before publishing.</p>}

      <ol className="space-y-2 p-5" aria-label={`Questions in ${section.title}`}>
        {shown.length === 0 && <li className="rounded-xl border border-dashed border-foreground-200 p-4 text-center text-[12px] text-foreground-400">Nothing in this section yet. Add a question below.</li>}
        {shown.map((item, i) => (
          <li key={item.key}>
            {editing === item.key ? (
              <QuestionEditor layout={layout} item={item} onSave={saveQuestion} onCancel={() => setEditing(null)} />
            ) : (
              <ItemRow
                layout={layout}
                section={section}
                item={item}
                first={i === 0}
                last={i === shown.length - 1}
                edit={edit}
                onEdit={() => setEditing(item.key)}
              />
            )}
          </li>
        ))}
      </ol>

      <div className="px-5 pb-5">
        {editing === 'new' ? (
          <QuestionEditor layout={layout} onSave={saveQuestion} onCancel={() => setEditing(null)} />
        ) : (
          <button ref={addRef} type="button" className={btnSecondary} onClick={() => setEditing('new')}>
            <AppIcon className="ri-add-line" />Add question
          </button>
        )}
      </div>

      {removed.length > 0 && (
        <div className="border-t border-foreground-100 px-5 py-4">
          <button type="button" className="cursor-pointer text-[12px] font-medium text-foreground-600 hover:text-primary-700" aria-expanded={showRemoved} onClick={() => setShowRemoved((v) => !v)}>
            <AppIcon className={showRemoved ? 'ri-arrow-down-s-line' : 'ri-arrow-right-s-line'} /> Removed from this section ({removed.length})
          </button>
          {showRemoved && (
            <ul className="mt-2 space-y-1.5">
              {removed.map((item) => (
                <li key={item.key} className="flex items-center justify-between gap-3 rounded-lg border border-foreground-100 px-3 py-2">
                  <span className="min-w-0 truncate text-[12px] text-foreground-500">{itemLabel(layout, item)}</span>
                  <button type="button" className="shrink-0 cursor-pointer text-[12px] font-medium text-primary-600 hover:underline" onClick={() => edit((l) => updateItem(l, item.key, { hidden: false }))}>
                    Restore
                  </button>
                </li>
              ))}
            </ul>
          )}
          <p className="mt-2 text-[11px] text-foreground-400">Removed questions keep their database column and every answer already given; restoring one brings them back.</p>
        </div>
      )}
    </section>
  );
}

function ItemRow({
  layout,
  section,
  item,
  first,
  last,
  edit,
  onEdit,
}: {
  layout: EligibilityLayout;
  section: EligibilitySection;
  item: EligibilityItem;
  first: boolean;
  last: boolean;
  edit: (fn: (l: EligibilityLayout) => EligibilityLayout) => void;
  onEdit: () => void;
}) {
  const def = item.builtin ? BUILTIN_ITEMS[item.key] : undefined;
  const question = isBuiltinQuestion(def) ? def : undefined;
  const note = def?.kind === 'note';
  const label = itemLabel(layout, item);
  const locked = isLocked(item);
  const required = locked || (item.builtin ? item.required ?? question?.required ?? false : Boolean(item.required));
  const deps = dependents(layout, item.key);
  const slots = item.builtin ? ITEM_TEXT_SLOTS[item.key] ?? [] : [];
  const list = builtinOptions(item);
  const otherSections = layout.sections.filter((s) => s.slug !== section.slug);
  const trigger = item.condition ? findItem(layout, item.condition.field) : question?.followUpOf ? findItem(layout, question.followUpOf.key) : undefined;
  const shownFor = item.condition?.values ?? (question?.followUpOf ? [question.followUpOf.value ? 'Yes' : 'No'] : []);
  const hint = question ? textFor(layout.texts, `${item.key}.hint`) : item.helpText;
  // Information is all wording, so it opens with its wording showing.
  const [panel, setPanel] = useState<'wording' | 'answers' | null>(note ? 'wording' : null);
  const wordingEdited = slots.some((s) => typeof layout.texts?.[s.key] === 'string');

  const remove = () => {
    if (deps.length && !window.confirm(`${deps.length} question${deps.length === 1 ? '' : 's'} only asked for an answer to this question will stop being asked.\n\nRemove it from the form?`)) return;
    edit((l) => removeItem(l, item.key));
  };

  const toggle = (which: 'wording' | 'answers') => setPanel((p) => (p === which ? null : which));

  return (
    <div className="rounded-xl border border-foreground-100 bg-background-50 px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <div className="min-w-0 flex-1">
          <p className="truncate text-[13px] font-medium text-foreground-900">{label}</p>
          {hint && !note && <p className="mt-0.5 truncate text-[12px] text-foreground-500">{hint}</p>}
          <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[10.5px]">
            <span className="rounded-full bg-background-200 px-2 py-0.5 text-foreground-600">
              {note ? 'Information' : item.builtin ? 'Standard question' : `Custom · ${TYPE_LABEL[item.type ?? 'text']}`}
            </span>
            {!item.builtin && !item.column && <span className="rounded-full bg-primary-100 px-2 py-0.5 text-primary-700">New — adds a column when published</span>}
            {locked && <span className="rounded-full bg-background-200 px-2 py-0.5 text-foreground-600"><AppIcon className="ri-lock-line mr-0.5 inline" />Identifies the applicant — always asked</span>}
            {question?.postcode && <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-emerald-800">Postcode checked and located</span>}
            {trigger && shownFor.length > 0 && (
              <span className="rounded-full bg-sky-100 px-2 py-0.5 text-sky-800">
                Asked when “{itemLabel(layout, trigger)}” is {shownFor.join(' / ')}
              </span>
            )}
            {wordingEdited && <span className="rounded-full bg-primary-100 px-2 py-0.5 text-primary-700">Wording edited</span>}
            {item.builtin && item.options && <span className="rounded-full bg-primary-100 px-2 py-0.5 text-primary-700">Answers edited</span>}
          </div>
        </div>

        {!note && (
          <label className={`flex shrink-0 items-center gap-1.5 text-[12px] ${locked ? 'text-foreground-400' : 'text-foreground-700'}`}>
            <input
              type="checkbox"
              className="accent-primary-500"
              checked={required}
              disabled={locked}
              onChange={(e) => edit((l) => updateItem(l, item.key, { required: e.target.checked }))}
            />
            Mandatory
          </label>
        )}
        {otherSections.length > 0 && (
          <select
            aria-label={`Move ${label} to another section`}
            className={`${inputClass} !w-auto shrink-0 cursor-pointer !py-1.5 text-[12px]`}
            value=""
            onChange={(e) => e.target.value && edit((l) => moveItemToSection(l, item.key, e.target.value))}
          >
            <option value="">Move to section…</option>
            {otherSections.map((s) => <option key={s.slug} value={s.slug}>{s.title || 'Untitled section'}{s.hidden ? ' (hidden)' : ''}</option>)}
          </select>
        )}
        <div className="flex shrink-0 items-center">
          <button type="button" className={iconBtn} disabled={first} aria-label={`Move ${label} up`} onClick={() => edit((l) => moveItem(l, section.slug, item.key, -1))}>
            <AppIcon className="ri-arrow-up-line" />
          </button>
          <button type="button" className={iconBtn} disabled={last} aria-label={`Move ${label} down`} onClick={() => edit((l) => moveItem(l, section.slug, item.key, 1))}>
            <AppIcon className="ri-arrow-down-line" />
          </button>
          {slots.length > 0 && (
            <button
              type="button"
              className={`${iconBtn}${panel === 'wording' ? ' !bg-primary-50 !text-primary-700' : ''}`}
              aria-label={`Edit wording of ${label}`}
              aria-expanded={panel === 'wording'}
              title="Edit wording"
              onClick={() => toggle('wording')}
            >
              <AppIcon className="ri-edit-line" />
            </button>
          )}
          {list && (
            <button
              type="button"
              className={`${iconBtn}${panel === 'answers' ? ' !bg-primary-50 !text-primary-700' : ''}`}
              aria-label={`Edit answers of ${label}`}
              aria-expanded={panel === 'answers'}
              title="Edit answers"
              onClick={() => toggle('answers')}
            >
              <AppIcon className="ri-list-unordered" />
            </button>
          )}
          {!item.builtin && (
            <button type="button" className={iconBtn} aria-label={`Edit ${label}`} onClick={onEdit}>
              <AppIcon className="ri-pencil-line" />
            </button>
          )}
          {!locked && (
            <button type="button" className={`${iconBtn} hover:!text-red-600`} aria-label={`Remove ${label}`} onClick={remove}>
              <AppIcon className="ri-delete-bin-line" />
            </button>
          )}
        </div>
      </div>
      {panel === 'wording' && slots.length > 0 && (
        <div className="mt-3 space-y-3 border-t border-foreground-100 pt-3">
          {slots.map((slot) => (
            <TextEditor key={slot.key} slot={slot} texts={layout.texts} onChange={(v) => edit((l) => setText(l, slot.key, v))} />
          ))}
        </div>
      )}
      {panel === 'answers' && list && question?.options && (
        <div className="mt-3 space-y-2 border-t border-foreground-100 pt-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-[12px] font-medium text-foreground-600">Answers applicants choose from</p>
            {item.options && (
              <button type="button" className="cursor-pointer text-[11.5px] font-medium text-primary-600 hover:underline" onClick={() => edit((l) => updateItem(l, item.key, { options: undefined }))}>
                Reset to standard answers
              </button>
            )}
          </div>
          <OptionsEditor
            options={list}
            name={label}
            showInvalid
            onChange={(next) => {
              const standard = question.options!;
              const same = next.length === standard.length && next.every((o, i) => o === standard[i]);
              edit((l) => updateItem(l, item.key, { options: same ? undefined : next }));
            }}
          />
          <p className="text-[11px] text-foreground-400">Applications already submitted keep the answer they gave, even if it is no longer listed.</p>
        </div>
      )}
    </div>
  );
}

function PublishDialog({ layout, publishing, onCancel, onConfirm }: { layout: EligibilityLayout; publishing: boolean; onCancel: () => void; onConfirm: () => void }) {
  const columns = pendingColumns(layout);
  const issues = problems(layout);
  const confirmRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    confirmRef.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && !publishing) onCancel(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onCancel, publishing]);
  return (
    <div className="fixed inset-0 z-[200] flex items-center justify-center bg-black/40 p-4">
      <div role="dialog" aria-modal="true" aria-labelledby="publish-eligibility-title" className="w-full max-w-lg rounded-2xl bg-background-50 p-5 shadow-xl">
        <h2 id="publish-eligibility-title" className="font-heading text-[16px] font-semibold text-foreground-900">Publish the eligibility form?</h2>
        <p className="mt-2 text-[13px] text-foreground-600">
          Everyone who opens the form from now on sees these changes. Applications already submitted are not changed;
          anyone part-way through is asked to check their answers before submitting.
        </p>
        {columns.length > 0 && (
          <div className="mt-3 rounded-lg border border-foreground-100 bg-background-100/60 p-3 text-[12px] text-foreground-700">
            <p className="font-medium">New database columns ({columns.length}) will be added to enrolment.eligibility_form:</p>
            <ul className="mt-1 list-disc pl-5">
              {columns.map((c, i) => <li key={`${c.label}-${i}`}>{c.label} <span className="text-foreground-400">({TYPE_LABEL[c.type] ?? c.type})</span></li>)}
            </ul>
          </div>
        )}
        {issues.length > 0 && (
          <ul role="alert" className="mt-3 list-disc space-y-0.5 rounded-lg border border-red-200 bg-red-50 py-2 pl-7 pr-3 text-[12px] text-red-700">
            {issues.map((issue) => <li key={issue}>{issue}</li>)}
          </ul>
        )}
        <div className="mt-5 flex justify-end gap-2">
          <button type="button" className={btnSecondary} onClick={onCancel} disabled={publishing}>Cancel</button>
          <button ref={confirmRef} type="button" className={btnPrimary} onClick={onConfirm} disabled={publishing || issues.length > 0}>
            {publishing ? <><AppIcon className="ri-loader-4-line animate-spin" />Publishing…</> : <><AppIcon className="ri-upload-cloud-2-line" />Publish</>}
          </button>
        </div>
      </div>
    </div>
  );
}
