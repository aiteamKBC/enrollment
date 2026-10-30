import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { WorkspaceShell } from '@/components/feature/WorkspaceShell';
import { AppIcon } from '@/components/feature/AppIcon';
import { AdminPageHeader } from '@/pages/admin/_shared/AdminPage';
import { useToast } from "@/hooks/useToast.helpers";
import { roleNavMap } from '@/mocks/navigation';
import { fetchWizardLayout, publishWizardLayout } from '@/api/wizardLayout';
import { btnPrimary, btnSecondary, iconBtn, inputClass } from '../components/ui';
import { builtinDef, isRequired, itemLabel, resolveLayout } from '../wizard/layout/resolve';
import type { LayoutItem, LayoutStep, WizardLayout } from '../wizard/layout/types';
import { FieldEditor } from './FieldEditor';
import { TextEditor } from './TextEditor';
import { TEXT_SLOTS_BY_ITEM, textFor } from '../wizard/layout/texts';
import type { FieldDraft } from './fieldDraft';
import {
  addStep,
  canChangeStep,
  dependents,
  moveItem,
  moveItemToStep,
  moveStep,
  pendingColumns,
  removeItem,
  saveCustomField,
  setText,
  updateItem,
  updateStep,
} from './builderOps';

const enrolmentNav = roleNavMap.apprentice;

const KIND_LABEL: Record<string, string> = {
  field: 'Built-in question',
  heading: 'Section heading',
  content: 'Information',
  block: 'Block',
};

const TYPE_LABEL: Record<string, string> = { text: 'Text', number: 'Number', dropdown: 'Dropdown', upload: 'File upload' };

const clone = (layout: WizardLayout): WizardLayout => JSON.parse(JSON.stringify(layout)) as WizardLayout;

/**
 * The "Edit apprenticeship wizard" builder.
 *
 * Edits a copy of the published layout and publishes it in one go. Publishing
 * is what learners see — immediately, for everyone who has not yet submitted —
 * and is where the server adds the columns for new fields to each step's table
 * in the enrolment schema. Nothing is ever dropped: removing a field hides it
 * and keeps its column and answers.
 */
export default function WizardBuilderPage() {
  const navigate = useNavigate();
  const { success, error } = useToast();
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [version, setVersion] = useState<number | null>(null);
  const [updatedAt, setUpdatedAt] = useState('');
  const [updatedBy, setUpdatedBy] = useState('');
  const [published, setPublished] = useState<WizardLayout | null>(null);
  const [layout, setLayout] = useState<WizardLayout | null>(null);
  const [selected, setSelected] = useState<string>('');
  const [editing, setEditing] = useState<string | 'new' | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    fetchWizardLayout({ force: true })
      .then((res) => {
        if (cancelled) return;
        const resolved = resolveLayout(res.layout);
        setPublished(resolved);
        setLayout(clone(resolved));
        setVersion(res.version);
        setUpdatedAt(res.updatedAt);
        setUpdatedBy(res.updatedBy);
        setSelected((cur) => (resolved.steps.some((s) => s.slug === cur) ? cur : resolved.steps[0]?.slug ?? ''));
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
    if (dirty && !window.confirm('You have unpublished changes to the wizard. Leave without publishing them?')) return;
    navigate('/users');
  };

  const edit = useCallback((fn: (l: WizardLayout) => WizardLayout) => setLayout((l) => (l ? fn(l) : l)), []);

  const publish = async () => {
    if (!layout) return;
    setPublishing(true);
    try {
      const saved = await publishWizardLayout(layout, version);
      const resolved = resolveLayout(saved.layout);
      setPublished(resolved);
      setLayout(clone(resolved));
      setVersion(saved.version);
      setUpdatedAt(saved.updatedAt);
      setUpdatedBy(saved.updatedBy);
      // A new step's temporary slug is replaced by the server's.
      setSelected((cur) => (resolved.steps.some((s) => s.slug === cur) ? cur : resolved.steps[resolved.steps.length - 1]?.slug ?? ''));
      setConfirming(false);
      success('Wizard published', 'Learners who have not yet submitted will see the changes the next time they open their enrolment.');
    } catch (e) {
      error('Could not publish the wizard', e instanceof Error ? e.message : 'Unexpected error');
    } finally {
      setPublishing(false);
    }
  };

  const step = layout?.steps.find((s) => s.slug === selected) ?? null;

  return (
    <WorkspaceShell role="compliance" roleLabel={enrolmentNav.label} navItems={enrolmentNav.items} workspaceLabel={enrolmentNav.workspaceLabel} pageTitle="Apprenticeship wizard" pageSubtitle="Edit the enrolment wizard learners complete at onboarding">
      <div className="admin-console-page min-w-0 space-y-4 p-3 md:space-y-5 md:p-6">
        <AdminPageHeader
          title="Apprenticeship enrolment wizard"
          description="Add, remove, reorder and require the questions learners answer during onboarding. Published changes apply to every learner who has not yet submitted their enrolment."
          icon="ri-magic-line"
          eyebrow="Enrolment"
          actions={
            <div className="flex flex-wrap items-start gap-2">
              <button type="button" className={btnSecondary} onClick={leave}>
                <AppIcon className="ri-arrow-left-line" />Back to users
              </button>
              <button type="button" className={btnSecondary} disabled={!dirty || publishing} onClick={() => published && setLayout(clone(published))}>
                <AppIcon className="ri-arrow-go-back-line" />Discard changes
              </button>
              <button type="button" className={btnPrimary} disabled={!dirty || publishing} onClick={() => setConfirming(true)}>
                <AppIcon className="ri-upload-cloud-2-line" />Publish changes
              </button>
            </div>
          }
        />

        {loading && <p className="text-[13px] text-foreground-400"><AppIcon className="ri-loader-4-line mr-2 animate-spin" />Loading the wizard…</p>}
        {!loading && loadError && (
          <div className="rounded-2xl border border-red-200 bg-red-50 p-4 text-[13px] text-red-700">
            <AppIcon className="ri-error-warning-line mr-1.5" />{loadError}
            <button type="button" className={`${btnSecondary} ml-3`} onClick={() => setReloadToken((n) => n + 1)}><AppIcon className="ri-refresh-line" />Retry</button>
          </div>
        )}

        {!loading && !loadError && layout && (
          <>
            <p className="text-[12px] text-foreground-500" aria-live="polite">
              {dirty ? (
                <span className="font-medium text-amber-700"><AppIcon className="ri-edit-circle-line mr-1" />You have unpublished changes.</span>
              ) : version ? (
                <>Published version {version}{updatedAt ? ` · ${new Date(updatedAt).toLocaleString('en-GB')}` : ''}{updatedBy ? ` · ${updatedBy}` : ''}</>
              ) : (
                'Showing the standard wizard — nothing has been published from the builder yet.'
              )}
            </p>
            <div className="flex flex-col gap-4 lg:flex-row lg:items-start">
              <StepList
                layout={layout}
                selected={selected}
                onSelect={(slug) => { setSelected(slug); setEditing(null); }}
                onMove={(slug, delta) => edit((l) => moveStep(l, slug, delta))}
                onAdd={() => {
                  const res = addStep(layout, 'New step');
                  setLayout(res.layout);
                  setSelected(res.slug);
                  setEditing(null);
                }}
              />
              {step && (
                <StepEditor
                  key={step.slug}
                  layout={layout}
                  step={step}
                  editing={editing}
                  setEditing={setEditing}
                  edit={edit}
                />
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

function StepList({
  layout,
  selected,
  onSelect,
  onMove,
  onAdd,
}: {
  layout: WizardLayout;
  selected: string;
  onSelect: (slug: string) => void;
  onMove: (slug: string, delta: -1 | 1) => void;
  onAdd: () => void;
}) {
  let number = 0;
  return (
    <nav aria-label="Wizard steps" className="shrink-0 overflow-hidden rounded-2xl border border-foreground-200/60 bg-background-50 shadow-sm lg:w-72">
      <div className="border-b border-foreground-100 px-4 py-3">
        <p className="text-[11px] font-semibold uppercase tracking-wider text-foreground-400">Steps</p>
      </div>
      <ol className="space-y-0.5 p-2">
        {layout.steps.map((s, i) => {
          if (!s.hidden) number += 1;
          const active = s.slug === selected;
          return (
            <li key={s.slug} className={`flex items-center gap-1 rounded-xl ${active ? 'bg-primary-50' : 'hover:bg-background-100'}`}>
              <button
                type="button"
                onClick={() => onSelect(s.slug)}
                aria-current={active ? 'step' : undefined}
                aria-label={s.hidden ? `${s.label} (hidden)` : s.label}
                className={`flex min-w-0 flex-1 cursor-pointer items-center gap-2.5 px-2.5 py-2 text-left ${s.hidden ? 'text-foreground-300' : active ? 'text-primary-700' : 'text-foreground-700'}`}
              >
                <span className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-[11px] font-semibold ${active ? 'border-primary-300 bg-primary-100' : 'border-foreground-200'}`}>
                  {s.hidden ? <AppIcon className="ri-eye-off-line text-[11px]" /> : number}
                </span>
                <span className="min-w-0">
                  <span className={`block truncate text-[12.5px] ${active ? 'font-semibold' : 'font-medium'}`}>{s.label}</span>
                  <span className="block text-[10.5px] text-foreground-400">{s.hidden ? 'Hidden' : s.builtin ? 'Standard step' : 'Added step'}</span>
                </span>
              </button>
              <button type="button" className={iconBtn} disabled={i === 0} aria-label={`Move ${s.label} up`} onClick={() => onMove(s.slug, -1)}>
                <AppIcon className="ri-arrow-up-s-line" />
              </button>
              <button type="button" className={iconBtn} disabled={i === layout.steps.length - 1} aria-label={`Move ${s.label} down`} onClick={() => onMove(s.slug, 1)}>
                <AppIcon className="ri-arrow-down-s-line" />
              </button>
            </li>
          );
        })}
      </ol>
      <div className="border-t border-foreground-100 p-2">
        <button type="button" onClick={onAdd} className="flex w-full cursor-pointer items-center justify-center gap-1.5 rounded-xl border border-dashed border-foreground-200 px-3 py-2 text-[12px] font-medium text-primary-700 hover:bg-primary-50">
          <AppIcon className="ri-add-line" />Add step
        </button>
      </div>
    </nav>
  );
}

function StepEditor({
  layout,
  step,
  editing,
  setEditing,
  edit,
}: {
  layout: WizardLayout;
  step: LayoutStep;
  editing: string | 'new' | null;
  setEditing: (v: string | 'new' | null) => void;
  edit: (fn: (l: WizardLayout) => WizardLayout) => void;
}) {
  const shown = step.items.filter((it) => !it.hidden);
  const removed = step.items.filter((it) => it.hidden);
  const [showRemoved, setShowRemoved] = useState(false);
  const addRef = useRef<HTMLButtonElement>(null);

  const saveField = (draft: FieldDraft) => {
    const fields: Partial<LayoutItem> = {
      label: draft.label,
      type: draft.type,
      options: draft.type === 'dropdown' ? draft.options : undefined,
      helpText: draft.helpText || undefined,
      required: draft.required,
      condition: draft.condition,
    };
    if (editing) {
      const key = editing === 'new' ? null : editing;
      edit((l) => saveCustomField(l, step.slug, key, fields, draft.followUps, draft.removedFollowUps).layout);
    }
    setEditing(null);
    addRef.current?.focus();
  };

  return (
    <section aria-label={`${step.label} step`} className="min-w-0 flex-1 overflow-hidden rounded-2xl border border-foreground-200/60 bg-background-50 shadow-sm">
      <div className="flex flex-wrap items-end gap-3 border-b border-foreground-100 px-5 py-4">
        <label className="min-w-[220px] flex-1 text-[12px] font-medium text-foreground-600" htmlFor="builder-step-name">
          Step name
          <input id="builder-step-name" className={`${inputClass} mt-1`} value={step.label} onChange={(e) => edit((l) => updateStep(l, step.slug, { label: e.target.value }))} />
        </label>
        <label className="flex items-center gap-2 pb-2 text-[12px] font-medium text-foreground-700">
          <input type="checkbox" className="accent-primary-500" checked={!step.hidden} onChange={(e) => edit((l) => updateStep(l, step.slug, { hidden: !e.target.checked }))} />
          Show this step to learners
        </label>
      </div>
      {(TEXT_SLOTS_BY_ITEM[`step:${step.slug}`] ?? []).length > 0 && (
        <div className="space-y-3 border-b border-foreground-100 px-5 py-4">
          {TEXT_SLOTS_BY_ITEM[`step:${step.slug}`].map((slot) => (
            <TextEditor key={slot.key} slot={slot} texts={layout.texts} onChange={(v) => edit((l) => setText(l, slot.key, v))} />
          ))}
        </div>
      )}
      {step.hidden && (
        <p className="mx-5 mt-4 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[12px] text-amber-800">
          <AppIcon className="ri-eye-off-line mr-1" />This step is hidden. Learners skip it, and none of its questions are required. Answers already given are kept.
          {step.items.some((it) => !it.hidden && builtinDef(it)?.onIlrDocument) && (
            <> Some of its questions are printed on the ILR document and will be left blank there.</>
          )}
        </p>
      )}
      {!step.label.trim() && <p role="alert" className="mx-5 mt-3 text-[12px] text-red-600">Give the step a name before publishing.</p>}

      <ol className="space-y-2 p-5" aria-label={`Items on ${step.label}`}>
        {shown.length === 0 && <li className="rounded-xl border border-dashed border-foreground-200 p-4 text-center text-[12px] text-foreground-400">Nothing on this step yet. Add a field below.</li>}
        {shown.map((item, i) => (
          <li key={item.key}>
            {editing === item.key ? (
              <FieldEditor layout={layout} item={item} onSave={saveField} onCancel={() => setEditing(null)} />
            ) : (
              <ItemRow
                layout={layout}
                step={step}
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
          <FieldEditor layout={layout} onSave={saveField} onCancel={() => setEditing(null)} />
        ) : (
          <button ref={addRef} type="button" className={btnSecondary} onClick={() => setEditing('new')}>
            <AppIcon className="ri-add-line" />Add field
          </button>
        )}
      </div>

      {removed.length > 0 && (
        <div className="border-t border-foreground-100 px-5 py-4">
          <button type="button" className="cursor-pointer text-[12px] font-medium text-foreground-600 hover:text-primary-700" aria-expanded={showRemoved} onClick={() => setShowRemoved((v) => !v)}>
            <AppIcon className={showRemoved ? 'ri-arrow-down-s-line' : 'ri-arrow-right-s-line'} /> Removed from this step ({removed.length})
          </button>
          {showRemoved && (
            <ul className="mt-2 space-y-1.5">
              {removed.map((item) => (
                <li key={item.key} className="flex items-center justify-between gap-3 rounded-lg border border-foreground-100 px-3 py-2">
                  <span className="min-w-0 truncate text-[12px] text-foreground-500">{itemLabel(item)}</span>
                  <button type="button" className="shrink-0 cursor-pointer text-[12px] font-medium text-primary-600 hover:underline" onClick={() => edit((l) => updateItem(l, item.key, { hidden: false }))}>
                    Restore
                  </button>
                </li>
              ))}
            </ul>
          )}
          <p className="mt-2 text-[11px] text-foreground-400">Removed fields keep their database column and every answer already given; restoring one brings them back.</p>
        </div>
      )}
    </section>
  );
}

function ItemRow({
  layout,
  step,
  item,
  first,
  last,
  edit,
  onEdit,
}: {
  layout: WizardLayout;
  step: LayoutStep;
  item: LayoutItem;
  first: boolean;
  last: boolean;
  edit: (fn: (l: WizardLayout) => WizardLayout) => void;
  onEdit: () => void;
}) {
  const def = builtinDef(item);
  const label = itemLabel(item);
  const requirable = item.builtin ? Boolean(def?.requirable) : true;
  const required = isRequired(item);
  const onIlr = Boolean(def?.onIlrDocument);
  const relaxedIlr = onIlr && def?.defaultRequired && !required;
  const deps = dependents(layout, item.key);
  const condition = item.condition;
  const trigger = condition ? layout.steps.flatMap((s) => s.items).find((it) => it.key === condition.field) : undefined;
  const otherSteps = layout.steps.filter((s) => s.slug !== step.slug);
  const slots = item.builtin ? TEXT_SLOTS_BY_ITEM[item.key] ?? [] : [];
  // Blocks and information are mostly wording, so it is shown open; a
  // question shows its wording in one line until asked to edit it.
  const wordy = def?.kind === 'block' || def?.kind === 'content';
  const [showWording, setShowWording] = useState(wordy);
  const editedCount = slots.filter((s) => typeof layout.texts?.[s.key] === 'string').length;

  const remove = () => {
    const warnings: string[] = [];
    if (onIlr) warnings.push(`“${label}” is printed on the ILR document. Without it, that part of the document will be left blank.`);
    if (deps.length) warnings.push(`${deps.length} field${deps.length === 1 ? '' : 's'} only shown for an answer to this question will stop showing.`);
    if (warnings.length && !window.confirm(`${warnings.join('\n\n')}\n\nRemove it from the wizard?`)) return;
    edit((l) => removeItem(l, item.key));
  };

  return (
    <div className="rounded-xl border border-foreground-100 bg-background-50 px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <div className="min-w-0 flex-1">
          <p className="truncate text-[13px] font-medium text-foreground-900">{label}</p>
          {slots.length > 0 && !wordy && (
            <p className="mt-0.5 truncate text-[12px] text-foreground-500">“{textFor(layout.texts, slots[0].key)}”</p>
          )}
          <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[10.5px]">
            <span className="rounded-full bg-background-200 px-2 py-0.5 text-foreground-600">
              {item.builtin ? KIND_LABEL[def?.kind ?? 'field'] : `Custom · ${TYPE_LABEL[item.type ?? 'text']}`}
            </span>
            {!item.builtin && !item.column && <span className="rounded-full bg-primary-100 px-2 py-0.5 text-primary-700">New — adds a column when published</span>}
            {onIlr && <span className="rounded-full bg-amber-100 px-2 py-0.5 text-amber-800"><AppIcon className="ri-file-text-line mr-0.5" />On ILR document</span>}
            {condition && trigger && (
              <span className="rounded-full bg-sky-100 px-2 py-0.5 text-sky-800">
                Shown when “{itemLabel(trigger)}” is {condition.values.join(' / ')}
              </span>
            )}
            {item.builtin && def?.kind === 'field' && <span className="text-foreground-400">{def.defaultRequired ? 'Mandatory by default' : 'Optional by default'}</span>}
            {editedCount > 0 && <span className="rounded-full bg-primary-100 px-2 py-0.5 text-primary-700">Wording edited</span>}
          </div>
        </div>

        {requirable && (
          <label className="flex shrink-0 items-center gap-1.5 text-[12px] text-foreground-700">
            <input
              type="checkbox"
              className="accent-primary-500"
              checked={required}
              onChange={(e) => edit((l) => updateItem(l, item.key, { required: e.target.checked }))}
            />
            Mandatory
          </label>
        )}
        {canChangeStep(item) && otherSteps.length > 0 && (
          <select
            aria-label={`Move ${label} to another step`}
            className={`${inputClass} !w-auto shrink-0 cursor-pointer !py-1.5 text-[12px]`}
            value=""
            onChange={(e) => e.target.value && edit((l) => moveItemToStep(l, item.key, e.target.value))}
          >
            <option value="">Move to step…</option>
            {otherSteps.map((s) => <option key={s.slug} value={s.slug}>{s.label}</option>)}
          </select>
        )}
        <div className="flex shrink-0 items-center">
          <button type="button" className={iconBtn} disabled={first} aria-label={`Move ${label} up`} onClick={() => edit((l) => moveItem(l, step.slug, item.key, -1))}>
            <AppIcon className="ri-arrow-up-line" />
          </button>
          <button type="button" className={iconBtn} disabled={last} aria-label={`Move ${label} down`} onClick={() => edit((l) => moveItem(l, step.slug, item.key, 1))}>
            <AppIcon className="ri-arrow-down-line" />
          </button>
          {slots.length > 0 && (
            <button
              type="button"
              className={`${iconBtn}${showWording ? ' !bg-primary-50 !text-primary-700' : ''}`}
              aria-label={`Edit wording of ${label}`}
              aria-expanded={showWording}
              title="Edit wording"
              onClick={() => setShowWording((v) => !v)}
            >
              <AppIcon className="ri-text" />
            </button>
          )}
          {!item.builtin && (
            <button type="button" className={iconBtn} aria-label={`Edit ${label}`} onClick={onEdit}>
              <AppIcon className="ri-pencil-line" />
            </button>
          )}
          <button type="button" className={`${iconBtn} hover:!text-red-600`} aria-label={`Remove ${label}`} onClick={remove}>
            <AppIcon className="ri-delete-bin-line" />
          </button>
        </div>
      </div>
      {showWording && slots.length > 0 && (
        <div className="mt-3 space-y-3 border-t border-foreground-100 pt-3">
          {slots.map((slot) => (
            <TextEditor key={slot.key} slot={slot} texts={layout.texts} onChange={(v) => edit((l) => setText(l, slot.key, v))} />
          ))}
          {onIlr && def?.step === 'ilr' && (
            <p className="text-[11px] text-amber-700">Edited question wording is also printed on newly generated Extended ILR documents.</p>
          )}
        </div>
      )}
      {relaxedIlr && (
        <p className="mt-2 rounded-lg bg-amber-50 px-2.5 py-1.5 text-[11.5px] text-amber-800">
          <AppIcon className="ri-error-warning-line mr-1" />Optional now: if a learner leaves this blank, it prints blank on the ILR document.
        </p>
      )}
    </div>
  );
}

function PublishDialog({ layout, publishing, onCancel, onConfirm }: { layout: WizardLayout; publishing: boolean; onCancel: () => void; onConfirm: () => void }) {
  const columns = pendingColumns(layout);
  const unnamed = layout.steps.some((s) => !s.label.trim());
  const confirmRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    confirmRef.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && !publishing) onCancel(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onCancel, publishing]);
  return (
    <div className="fixed inset-0 z-[200] flex items-center justify-center bg-black/40 p-4">
      <div role="dialog" aria-modal="true" aria-labelledby="publish-wizard-title" className="w-full max-w-lg rounded-2xl bg-background-50 p-5 shadow-xl">
        <h2 id="publish-wizard-title" className="font-heading text-[16px] font-semibold text-foreground-900">Publish the wizard?</h2>
        <p className="mt-2 text-[13px] text-foreground-600">
          Every learner who has not yet submitted their enrolment will see these changes the next time they open the wizard.
          Enrolments already submitted are not reopened.
        </p>
        {columns.length > 0 && (
          <div className="mt-3 rounded-lg border border-foreground-100 bg-background-100/60 p-3 text-[12px] text-foreground-700">
            <p className="font-medium">New database columns ({columns.length}) will be added to the step tables in the enrolment schema:</p>
            <ul className="mt-1 list-disc pl-5">
              {columns.map((c, i) => <li key={`${c.label}-${i}`}>{c.label} <span className="text-foreground-400">({TYPE_LABEL[c.type] ?? c.type})</span></li>)}
            </ul>
          </div>
        )}
        {unnamed && <p role="alert" className="mt-3 text-[12px] text-red-600">Every step needs a name before the wizard can be published.</p>}
        <div className="mt-5 flex justify-end gap-2">
          <button type="button" className={btnSecondary} onClick={onCancel} disabled={publishing}>Cancel</button>
          <button ref={confirmRef} type="button" className={btnPrimary} onClick={onConfirm} disabled={publishing || unnamed}>
            {publishing ? <><AppIcon className="ri-loader-4-line animate-spin" />Publishing…</> : <><AppIcon className="ri-upload-cloud-2-line" />Publish</>}
          </button>
        </div>
      </div>
    </div>
  );
}
