import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { SuitabilityAdmin } from '@/features/programme-suitability/SuitabilityAdmin';
import { ModulesAdmin } from '@/features/programme-suitability/modules/ModulesAdmin';
import { CatalogueAdmin } from '@/features/programme-suitability/skills-radar/catalogue/CatalogueAdmin';
import { configurationErrors } from '@/features/programme-suitability/modules/configuration';
import { WorkspaceShell } from '@/components/feature/WorkspaceShell';
import { AppIcon } from '@/components/feature/AppIcon';
import { AdminPageHeader } from '@/pages/admin/_shared/AdminPage';
import { useToast } from "@/hooks/useToast.helpers";
import { roleNavMap } from '@/mocks/navigation';
import * as WizardLayoutApi from '@/api/wizardLayout';
import { fetchWizardLayout, publishWizardLayout } from '@/api/wizardLayout';
import type { WizardLayoutVersion } from '@/api/wizardLayout';
import { fetchEnrolmentUsers } from '@/api/enrolmentUsers';
import type { LearnerKind } from '@/api/extendedIlr';
import type { UserListRow } from '@/pages/users/types';
import { btnPrimary, btnSecondary, iconBtn, inputClass } from '../components/ui';
import { builtinDef, isRequired, itemLabel, resolveLayout } from '../wizard/layout/resolve';
import { isChoiceFieldType, isContentFieldType, type LayoutItem, type LayoutStep, type WizardLayout } from '../wizard/layout/types';
import { FieldEditor } from './FieldEditor';
import { TextEditor } from './TextEditor';
import { TEXT_SLOTS_BY_ITEM, textFor } from '../wizard/layout/texts';
import type { FieldDraft } from './fieldDraft';
import {
  addStep,
  canChangeStep,
  dependents,
  duplicateItem,
  moveItemBefore,
  moveItemAfter,
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

const TYPE_LABEL: Record<string, string> = {
  text: 'Single-line text (existing)',
  single_line: 'Single-line text',
  multi_line: 'Multi-line text',
  number: 'Number',
  dropdown: 'Dropdown',
  upload: 'File upload',
  heading: 'Heading',
  description: 'Description',
  divider: 'Divider',
  radio: 'Radio (single choice)',
  checkbox: 'Checkbox (multiple choice)',
};

const clone = (layout: WizardLayout): WizardLayout => JSON.parse(JSON.stringify(layout)) as WizardLayout;

/**
 * The "Edit apprenticeship wizard" builder.
 *
 * Edits a copy of the published layout and publishes an append-only version.
 * Unsubmitted learners without an override or checkpoint pin use the latest
 * global version; reaching Skills Radar pins their current version. Submitted
 * learners retain their submission version. Removing a field hides it and
 * keeps its column and answers.
 */
export default function WizardBuilderPage() {
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
  const [versionAction, setVersionAction] = useState<'create' | 'assign' | null>(null);
  const closeVersionDialog = useCallback(() => setVersionAction(null), []);
  const reportVersionError = useCallback((message: string) => error('Could not update wizard version', message), [error]);
  const reportVersionSuccess = useCallback((message: string) => {
    setVersionAction(null);
    success('Wizard version updated', message);
  }, [success]);

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
      success('Wizard published', 'Learners who have not reached Skills Radar will use this version. Others keep their pinned version.');
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
          description="Edit the onboarding wizard. Learners use the latest version until they reach Skills Radar; submitted learners keep their submitted version."
          icon="ri-magic-line"
          eyebrow="Enrolment"
          actions={
            <div className="flex flex-wrap items-start gap-2">
              <button type="button" className={btnSecondary} disabled={!layout || !version} onClick={() => setVersionAction('create')}>
                <AppIcon className="ri-git-branch-line" />Create targeted version
              </button>
              <button type="button" className={btnSecondary} onClick={() => setVersionAction('assign')}>
                <AppIcon className="ri-user-settings-line" />Assign a version
              </button>
              <button type="button" className={btnSecondary} disabled={!dirty || publishing} onClick={() => published && setLayout(clone(published))}>
                <AppIcon className="ri-arrow-go-back-line" />Discard changes
              </button>
              <button type="button" className={btnPrimary} disabled={!dirty || publishing || Boolean(layout?.programmeModules && configurationErrors(layout.programmeModules).length)} onClick={() => setConfirming(true)}>
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
            <p className="-mt-2 text-[12px] text-foreground-500">Unsubmitted learners use the latest global version until the successful step change that takes them to Skills Radar, which pins their version. Admins can assign a different published version to unsubmitted learners.</p>
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
                  version={version}
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
        {versionAction && layout && (
          <WizardVersionDialog
            mode={versionAction}
            layout={layout}
            parentVersion={version}
            onCancel={closeVersionDialog}
            onSuccess={reportVersionSuccess}
            onError={reportVersionError}
          />
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
  version,
  layout,
  step,
  editing,
  setEditing,
  edit,
}: {
  version: number | null;
  layout: WizardLayout;
  step: LayoutStep;
  editing: string | 'new' | null;
  setEditing: (v: string | 'new' | null) => void;
  edit: (fn: (l: WizardLayout) => WizardLayout) => void;
}) {
  const shown = step.items.filter((it) => !it.hidden);
  const removed = step.items.filter((it) => it.hidden);
  const [showRemoved, setShowRemoved] = useState(false);
  const [draggedKey, setDraggedKey] = useState<string | null>(null);
  const addRef = useRef<HTMLButtonElement>(null);

  const saveField = (draft: FieldDraft) => {
    const fields: Partial<LayoutItem> = {
      label: draft.label,
      type: draft.type,
      options: draft.type === 'dropdown' || isChoiceFieldType(draft.type) ? draft.options : undefined,
      helpText: isContentFieldType(draft.type) ? undefined : draft.helpText || undefined,
      required: isContentFieldType(draft.type) ? false : draft.required,
      condition: isContentFieldType(draft.type) ? null : draft.condition,
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
          <input type="checkbox" className="accent-primary-500" checked={!step.hidden} disabled={step.slug === 'programme-suitability' && layout.programmeSuitability?.enabled} onChange={(e) => edit((l) => updateStep(l, step.slug, { hidden: !e.target.checked }))} />
          Show this step to learners
        </label>
      </div>
      {step.slug === 'personal-details' && <p className="mx-5 mt-4 rounded-lg border border-foreground-200 p-3 text-[12px] text-foreground-600">
        This step applies to commercial onboarding only. Apprenticeship learners skip Personal Details. Your signature is required at the end of ILR and covers the ILR accuracy declaration and enrolment paperwork; its existing storage identifier is retained.
      </p>}
      {step.slug === 'ilr-details' && <p className="mx-5 mt-4 rounded-lg border border-foreground-200 p-3 text-[12px] text-foreground-600">
        Apprenticeships have one required Your signature field at the end of ILR. The legacy Declaration signature item displays the accuracy declaration only; publishing or restoring it cannot add another signing box. Historical signatures are preserved and require explicit confirmation for the combined wording.
      </p>}
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

      {step.slug === 'programme-suitability' && <SuitabilityAdmin value={layout.programmeSuitability} onChange={(configuration) => edit((l) => ({
        ...l, programmeSuitability: configuration,
        steps: l.steps.map((s) => s.slug === 'programme-suitability' && configuration.enabled ? { ...s, hidden: false } : s),
      }))} />}
      {step.slug === 'programme-suitability' && <ModulesAdmin version={version} value={layout.programmeModules}
        onChange={(programmeModules) => edit((l) => ({ ...l, programmeModules }))} />}
      {step.items.some((item) => item.key === 'block.skillsRadar') && <CatalogueAdmin layout={layout} version={version}
        onChange={(configuration) => edit((l) => ({ ...l, ...configuration }))} />}
      <ol
        className="space-y-2 p-5"
        aria-label={`Items on ${step.label}`}
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          setDraggedKey(null);
        }}
      >
        {shown.length === 0 && <li className="rounded-xl border border-dashed border-foreground-200 p-4 text-center text-[12px] text-foreground-400">Nothing on this step yet. Add a field below.</li>}
        {shown.map((item) => (
          <li
            key={item.key}
            draggable={editing !== item.key}
            onDragStart={(e) => {
              setDraggedKey(item.key);
              e.dataTransfer.effectAllowed = 'move';
              e.dataTransfer.setData('text/plain', item.key);
            }}
            onDragEnd={() => setDraggedKey(null)}
            onDragOver={(e) => {
              e.preventDefault();
              e.dataTransfer.dropEffect = 'move';
            }}
            onDrop={(e) => {
              e.preventDefault();
              e.stopPropagation();
              const sourceKey = draggedKey || e.dataTransfer.getData('text/plain');
              if (sourceKey && sourceKey !== item.key) {
                const rect = e.currentTarget.getBoundingClientRect();
                const placeAfter = typeof e.clientY === 'number' && e.clientY > rect.top + rect.height / 2;
                edit((l) => placeAfter
                  ? moveItemAfter(l, step.slug, sourceKey, item.key)
                  : moveItemBefore(l, step.slug, sourceKey, item.key));
              }
              setDraggedKey(null);
            }}
            className={draggedKey === item.key ? 'opacity-50' : undefined}
          >
            {editing === item.key ? (
              <FieldEditor layout={layout} item={item} onSave={saveField} onCancel={() => setEditing(null)} />
            ) : (
              <ItemRow
                layout={layout}
                step={step}
                item={item}
                edit={edit}
                onEdit={() => setEditing(item.key)}
                onDuplicate={() => {
                  const result = duplicateItem(layout, step.slug, item.key);
                  if (!result) return;
                  edit(() => result.layout);
                  setEditing(result.key);
                }}
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
  edit,
  onEdit,
  onDuplicate,
}: {
  layout: WizardLayout;
  step: LayoutStep;
  item: LayoutItem;
  edit: (fn: (l: WizardLayout) => WizardLayout) => void;
  onEdit: () => void;
  onDuplicate: () => void;
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
    <div className="flex overflow-hidden rounded-xl border border-foreground-100 bg-background-50">
      <span className="flex w-14 shrink-0 cursor-grab items-center justify-center border-r border-foreground-100 bg-background-100/40 text-foreground-400 active:cursor-grabbing" aria-label={`Drag ${label} to reorder`} title="Drag to reorder">
        <AppIcon className="ri-drag-move-2-line text-[20px]" />
      </span>
      <div className="min-w-0 flex-1 px-3 py-2.5">
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
            {!item.builtin && !item.column && !isContentFieldType(item.type) && <span className="rounded-full bg-primary-100 px-2 py-0.5 text-primary-700">New — adds a column when published</span>}
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

        {requirable && !isContentFieldType(item.type) && (
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
          {slots.length > 0 && (
            <button
              type="button"
              className={`${iconBtn}${showWording ? ' !bg-primary-50 !text-primary-700' : ''}`}
              aria-label={`Edit wording of ${label}`}
              aria-expanded={showWording}
              title="Edit wording"
              onClick={() => setShowWording((v) => !v)}
            >
              <AppIcon className="ri-pencil-line" />
            </button>
          )}
          {!item.builtin && (
            <>
              <button type="button" className={iconBtn} aria-label={`Edit ${label}`} onClick={onEdit}>
                <AppIcon className="ri-pencil-line" />
              </button>
              <button type="button" className={iconBtn} aria-label={`Duplicate ${label}`} title="Duplicate field" onClick={onDuplicate}>
                <AppIcon className="ri-file-copy-line" />
              </button>
            </>
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
          Learners who have not reached Skills Radar will use this global version next time they open the wizard. Reaching Skills Radar saves and pins the version they used; submitted learners retain their submitted version.
        </p>
        {layout.skillsRadarCatalogues && <p className="mt-2 text-[13px] text-foreground-600">Skills Radar assessments already started keep their saved catalogue version and answers.</p>}
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

function WizardVersionDialog({
  mode, layout, parentVersion, onCancel, onSuccess, onError,
}: {
  mode: 'create' | 'assign';
  layout: WizardLayout;
  parentVersion: number | null;
  onCancel: () => void;
  onSuccess: (message: string) => void;
  onError: (message: string) => void;
}) {
  const [users, setUsers] = useState<UserListRow[]>([]);
  const [versions, setVersions] = useState<WizardLayoutVersion[]>([]);
  const [kind, setKind] = useState<LearnerKind>('apprenticeship');
  const [selected, setSelected] = useState<string[]>([]);
  const [search, setSearch] = useState('');
  const [name, setName] = useState('');
  const [reason, setReason] = useState('');
  const [version, setVersion] = useState<number | null>(null);
  const [clearAssignment, setClearAssignment] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const savingRef = useRef(saving);
  savingRef.current = saving;
  const confirmRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([fetchEnrolmentUsers(), WizardLayoutApi.fetchWizardVersions()])
      .then(([rows, available]) => {
        if (cancelled) return;
        setUsers(rows.filter((row) => row.source === 'apprenticeship' || row.source === 'commercial'));
        setVersions(available);
        const first = available[0]?.version;
        setVersion(first ?? null);
      })
      .catch((e: unknown) => { if (!cancelled) onError(e instanceof Error ? e.message : 'Could not load users and versions.'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    confirmRef.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && !savingRef.current) onCancel(); };
    window.addEventListener('keydown', onKey);
    return () => { cancelled = true; window.removeEventListener('keydown', onKey); };
  }, [onCancel, onError]);

  const visibleUsers = users.filter((row) => row.source === kind && `${row.name} ${row.email}`.toLowerCase().includes(search.toLowerCase()));
  const chosenVersion = versions.find((item) => item.version === version);
  const toggleUser = (id: string) => setSelected((old) => old.includes(id) ? old.filter((item) => item !== id) : [...old, id]);

  const submit = async () => {
    if (!selected.length) return;
    setSaving(true);
    try {
      if (mode === 'create') {
        if (!parentVersion || !name.trim()) return;
        await publishWizardLayout(layout, parentVersion, {
          scope: 'targeted', name: name.trim(), parentVersion,
          assignTo: { learnerKind: kind, learnerIds: selected, reason: reason.trim() },
        });
        onSuccess(`Created “${name.trim()}” and assigned it to ${selected.length} learner${selected.length === 1 ? '' : 's'}.`);
      } else {
        if (version === null && !clearAssignment) return;
        await WizardLayoutApi.assignWizardVersion({ learnerKind: kind, learnerIds: selected, version: clearAssignment ? null : version, reason: reason.trim() });
        onSuccess(clearAssignment
          ? `The explicit version assignment was removed from ${selected.length} learner${selected.length === 1 ? '' : 's'}.`
          : `Version ${version}${chosenVersion?.name ? ` · ${chosenVersion.name}` : ''} assigned to ${selected.length} learner${selected.length === 1 ? '' : 's'}.`);
      }
    } catch (e) {
      onError(e instanceof Error ? e.message : 'Unexpected error');
    } finally {
      setSaving(false);
    }
  };

  const title = mode === 'create' ? 'Create a targeted wizard version' : 'Assign a published wizard version';
  const canSubmit = !loading && !saving && selected.length > 0 && (mode === 'create' ? Boolean(parentVersion && name.trim()) : version !== null || clearAssignment);
  return (
    <div className="fixed inset-0 z-[210] flex items-center justify-center bg-black/40 p-4">
      <div role="dialog" aria-modal="true" aria-labelledby="wizard-version-title" className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-2xl bg-background-50 p-5 shadow-xl">
        <h2 id="wizard-version-title" className="font-heading text-[17px] font-semibold text-foreground-900">{title}</h2>
        <p className="mt-1 text-[12px] leading-relaxed text-foreground-600">
          {mode === 'create' ? 'This saves the current editor layout as a named version and assigns it to the selected learners.' : 'This assignment makes the selected version take precedence for these unsubmitted learners, including learners who have not reached Skills Radar.'}
        </p>
        {mode === 'create' ? (
          <label className="mt-4 block text-[12px] font-medium text-foreground-700">Version name
            <input autoFocus className={`${inputClass} mt-1`} maxLength={120} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Additional support information" />
          </label>
        ) : (
          <label className="mt-4 block text-[12px] font-medium text-foreground-700">Published version
            <select className={`${inputClass} mt-1`} value={clearAssignment ? 'default' : version ?? ''} onChange={(e) => {
              if (e.target.value === 'default') { setClearAssignment(true); setVersion(null); }
              else { setClearAssignment(false); setVersion(Number(e.target.value) || null); }
            }}>
              <option value="" disabled>Select a version</option>
              <option value="default">Use the learner’s normal version</option>
              {versions.map((entry) => <option key={entry.version} value={entry.version}>Version {entry.version} · {entry.name}{entry.createdAt ? ` · ${new Date(entry.createdAt).toLocaleDateString('en-GB')}` : ''}</option>)}
            </select>
          </label>
        )}
        <div className="mt-4 flex gap-2">
          {(['apprenticeship', 'commercial'] as LearnerKind[]).map((value) => (
            <button key={value} type="button" className={kind === value ? btnPrimary : btnSecondary} onClick={() => { setKind(value); setSelected([]); }}>
              {value === 'apprenticeship' ? 'Apprenticeship learners' : 'Commercial learners'}
            </button>
          ))}
        </div>
        <label className="mt-3 block text-[12px] font-medium text-foreground-700">Search learners
          <input className={`${inputClass} mt-1`} value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Name or email" />
        </label>
        <div className="mt-2 max-h-56 overflow-y-auto rounded-xl border border-foreground-200">
          {loading ? <p className="p-4 text-[12px] text-foreground-500">Loading learners and versions…</p> : visibleUsers.length ? visibleUsers.map((row) => (
            <label key={`${row.source}:${row.id}`} className="flex cursor-pointer items-center gap-2 border-b border-foreground-100 px-3 py-2 last:border-0 hover:bg-background-100">
              <input type="checkbox" className="accent-primary-600" checked={selected.includes(row.id)} onChange={() => toggleUser(row.id)} />
              <span className="min-w-0"><span className="block truncate text-[12px] font-medium text-foreground-900">{row.name || row.email}</span><span className="block truncate text-[11px] text-foreground-500">{row.email}</span></span>
            </label>
          )) : <p className="p-4 text-[12px] text-foreground-500">No learners match this search.</p>}
        </div>
        <p className="mt-1 text-[11px] text-foreground-500">{selected.length} learner{selected.length === 1 ? '' : 's'} selected</p>
        <label className="mt-3 block text-[12px] font-medium text-foreground-700">Reason (optional)
          <input className={`${inputClass} mt-1`} maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why this version is being assigned" />
        </label>
        <div className="mt-5 flex justify-end gap-2">
          <button type="button" className={btnSecondary} onClick={onCancel} disabled={saving}>Cancel</button>
          <button ref={confirmRef} type="button" className={btnPrimary} onClick={submit} disabled={!canSubmit}>
            {saving ? <><AppIcon className="ri-loader-4-line animate-spin" />Saving…</> : mode === 'create' ? 'Create and assign' : 'Assign version'}
          </button>
        </div>
      </div>
    </div>
  );
}
