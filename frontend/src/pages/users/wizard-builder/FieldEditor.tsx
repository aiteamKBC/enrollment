import { useMemo, useState } from 'react';
import { AppIcon } from '@/components/feature/AppIcon';
import { btnPrimary, btnSecondary, inputClass } from '../components/ui';
import { itemLabel } from '../wizard/layout/resolve';
import { CUSTOM_FIELD_TYPES, type CustomFieldType, type LayoutItem, type WizardLayout } from '../wizard/layout/types';
import { BUILTIN_ITEMS } from '../wizard/layout/registry';
import { conditionSources, existingFollowUps, type FollowUpDraft } from './builderOps';
import { fieldDraftFrom, type FieldDraft } from './fieldDraft';


interface OptionRow {
  id: string;
  value: string;
  follow: {
    on: boolean;
    key?: string;
    label: string;
    type: CustomFieldType;
    required: boolean;
    published: boolean;
    /** The follow-up's own choices, when it is a dropdown. */
    options: string[];
  };
}

let rowSeq = 0;
const newRow = (value: string, follow?: Partial<OptionRow['follow']>): OptionRow => ({
  id: `row-${(rowSeq += 1)}`,
  value,
  follow: { on: false, label: '', type: 'text', required: false, published: false, options: ['', ''], ...follow },
});

/** One row per option, with the follow-up the option already has (if any). */
function initialRows(layout: WizardLayout, item?: LayoutItem): OptionRow[] {
  const existing = item ? existingFollowUps(layout, item.key) : [];
  const rows = (item?.options ?? []).map((option) => {
    const f = existing.find((it) => it.condition?.values[0] === option);
    return newRow(option, f ? { on: true, key: f.key, label: f.label ?? '', type: f.type ?? 'text', required: Boolean(f.required), published: Boolean(f.column), options: f.options?.length ? f.options : ['', ''] } : undefined);
  });
  return rows.length ? rows : [newRow(''), newRow('')];
}

/**
 * Add or edit one custom field: label, type, dropdown options, help text,
 * mandatory, and an optional "only show when <dropdown> is …" condition.
 *
 * A field that has been published already has a column of its type, so its
 * type is fixed here (the server refuses a change too).
 */
export function FieldEditor({
  layout,
  item,
  onSave,
  onCancel,
}: {
  layout: WizardLayout;
  /** The field being edited; absent when adding. */
  item?: LayoutItem;
  onSave: (draft: FieldDraft) => void;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState<FieldDraft>(() => fieldDraftFrom(item));
  const [rows, setRows] = useState<OptionRow[]>(() => initialRows(layout, item));
  // The follow-ups this field had when the editor opened, to know which went away.
  const [initialFollowUps] = useState(() => (item ? existingFollowUps(layout, item.key).map((f) => f.key) : []));
  const setRow = (id: string, patch: Partial<OptionRow>) => setRows((rs) => rs.map((r) => (r.id === id ? { ...r, ...patch } : r)));
  const setFollow = (id: string, patch: Partial<OptionRow['follow']>) =>
    setRows((rs) => rs.map((r) => (r.id === id ? { ...r, follow: { ...r.follow, ...patch } } : r)));
  const [tried, setTried] = useState(false);
  const [optionFilter, setOptionFilter] = useState('');
  const published = Boolean(item?.column);
  const set = (patch: Partial<FieldDraft>) => setDraft((d) => ({ ...d, ...patch }));

  const sources = useMemo(() => conditionSources(layout, item?.key), [layout, item?.key]);
  const source = draft.condition ? sources.find((s) => s.item.key === draft.condition!.field) : undefined;
  const isDropdown = draft.type === 'dropdown';
  const filled = rows.filter((r) => r.value.trim());
  const options = filled.map((r) => r.value.trim());
  const duplicates = new Set(options.filter((o, i) => options.indexOf(o) !== i));

  const errors: string[] = [];
  if (!draft.label.trim()) errors.push('Enter a label for the field.');
  if (isDropdown && options.length === 0) errors.push('Add at least one dropdown option.');
  if (isDropdown && duplicates.size) errors.push(`Each option must be different (${[...duplicates].join(', ')}).`);
  if (isDropdown && filled.some((r) => r.follow.on && !r.follow.label.trim())) errors.push('Write the follow-up question for each ticked option.');
  const followOptions = (r: OptionRow) => r.follow.options.map((o) => o.trim()).filter(Boolean);
  const dropdownFollowUps = isDropdown ? filled.filter((r) => r.follow.on && r.follow.type === 'dropdown') : [];
  if (dropdownFollowUps.some((r) => followOptions(r).length === 0)) errors.push('Add at least one option to each follow-up dropdown.');
  if (dropdownFollowUps.some((r) => new Set(followOptions(r)).size !== followOptions(r).length)) errors.push('Each option of a follow-up dropdown must be different.');
  if (draft.condition && (!source || draft.condition.values.length === 0)) {
    errors.push('Choose the dropdown and at least one answer that shows this field.');
  }

  const submit = () => {
    setTried(true);
    if (errors.length) return;
    const followUps: FollowUpDraft[] = isDropdown
      ? filled.filter((r) => r.follow.on).map((r) => ({
          option: r.value.trim(),
          key: r.follow.key,
          label: r.follow.label.trim(),
          type: r.follow.type,
          required: r.follow.required,
          ...(r.follow.type === 'dropdown' ? { options: followOptions(r) } : {}),
        }))
      : [];
    const kept = new Set(followUps.map((f) => f.key).filter(Boolean));
    onSave({
      ...draft,
      label: draft.label.trim(),
      helpText: draft.helpText.trim(),
      options: isDropdown ? options : [],
      followUps,
      removedFollowUps: initialFollowUps.filter((k) => !kept.has(k)),
    });
  };

  const chooseSource = (key: string) => {
    if (!key) return set({ condition: null });
    const found = sources.find((s) => s.item.key === key);
    if (!found) return;
    const path = found.item.builtin ? BUILTIN_ITEMS[found.item.key]?.path : undefined;
    set({ condition: { field: key, values: [], ...(path ? { path } : {}) } });
  };

  const toggleValue = (value: string) => {
    if (!draft.condition) return;
    const values = draft.condition.values.includes(value)
      ? draft.condition.values.filter((v) => v !== value)
      : [...draft.condition.values, value];
    set({ condition: { ...draft.condition, values } });
  };

  const shownOptions = source ? source.options.filter((o) => o.toLowerCase().includes(optionFilter.toLowerCase())) : [];
  const idPrefix = `field-editor-${item?.key ?? 'new'}`;

  return (
    <div className="rounded-xl border border-primary-200 bg-primary-50/40 p-4" role="group" aria-label={item ? `Edit ${itemLabel(item)}` : 'New field'}>
      <p className="mb-3 text-[13px] font-semibold text-foreground-900">{item ? 'Edit field' : 'New field'}</p>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block text-[12px] font-medium text-foreground-600 sm:col-span-2" htmlFor={`${idPrefix}-label`}>
          Question / label
          <input
            id={`${idPrefix}-label`}
            className={`${inputClass} mt-1`}
            value={draft.label}
            onChange={(e) => set({ label: e.target.value })}
            aria-invalid={tried && !draft.label.trim() ? true : undefined}
            placeholder="e.g. How many years have you worked in this role?"
          />
        </label>
        <label className="block text-[12px] font-medium text-foreground-600" htmlFor={`${idPrefix}-type`}>
          Field type
          <select
            id={`${idPrefix}-type`}
            className={`${inputClass} mt-1 cursor-pointer disabled:cursor-not-allowed disabled:opacity-60`}
            value={draft.type}
            disabled={published}
            onChange={(e) => set({ type: e.target.value as CustomFieldType })}
          >
            {CUSTOM_FIELD_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
          </select>
          {published && <span className="mt-1 block text-[11px] font-normal text-foreground-400">Published fields keep their type — remove it and add a new field to change it.</span>}
        </label>
        <label className="flex items-center gap-2 self-end pb-2 text-[12px] font-medium text-foreground-700">
          <input type="checkbox" className="accent-primary-500" checked={draft.required} onChange={(e) => set({ required: e.target.checked })} />
          Mandatory — learners must answer before moving on
        </label>
        {draft.type === 'dropdown' && (
          <fieldset className="sm:col-span-2">
            <legend className="text-[12px] font-medium text-foreground-600">Dropdown options</legend>
            <ol className="mt-1 space-y-2">
              {rows.map((row, i) => {
                const optionId = `${idPrefix}-option-${row.id}`;
                const name = row.value.trim() || `option ${i + 1}`;
                return (
                  <li key={row.id} className="rounded-lg border border-foreground-200 bg-background-50 p-2.5">
                    <div className="flex flex-wrap items-center gap-2">
                      <input
                        id={optionId}
                        className={`${inputClass} min-w-[180px] flex-1`}
                        value={row.value}
                        placeholder={`Option ${i + 1}`}
                        aria-label={`Option ${i + 1}`}
                        aria-invalid={tried && (!row.value.trim() || duplicates.has(row.value.trim())) ? true : undefined}
                        onChange={(e) => setRow(row.id, { value: e.target.value })}
                      />
                      <label className="flex shrink-0 items-center gap-1.5 text-[12px] text-foreground-700">
                        <input
                          type="checkbox"
                          className="accent-primary-500"
                          checked={row.follow.on}
                          onChange={(e) => setFollow(row.id, { on: e.target.checked })}
                        />
                        Ask a follow-up question when this is chosen
                      </label>
                      <button
                        type="button"
                        className="flex h-8 w-8 shrink-0 cursor-pointer items-center justify-center rounded-lg text-foreground-400 hover:bg-background-100 hover:text-red-600 disabled:cursor-not-allowed disabled:opacity-40"
                        aria-label={`Remove option ${name}`}
                        disabled={rows.length === 1}
                        onClick={() => setRows((rs) => rs.filter((r) => r.id !== row.id))}
                      >
                        <AppIcon className="ri-delete-bin-line" />
                      </button>
                    </div>
                    {row.follow.on && (
                      <div className="mt-2 grid gap-2 border-l-2 border-primary-200 pl-3 sm:grid-cols-[1fr_auto_auto] sm:items-end">
                        <label className="block text-[11.5px] font-medium text-foreground-600">
                          Follow-up question for “{name}”
                          <input
                            className={`${inputClass} mt-1`}
                            value={row.follow.label}
                            placeholder="e.g. Please give details"
                            aria-invalid={tried && !row.follow.label.trim() ? true : undefined}
                            onChange={(e) => setFollow(row.id, { label: e.target.value })}
                          />
                        </label>
                        <label className="block text-[11.5px] font-medium text-foreground-600">
                          Answer type
                          <select
                            className={`${inputClass} mt-1 cursor-pointer disabled:cursor-not-allowed disabled:opacity-60`}
                            value={row.follow.type}
                            disabled={row.follow.published}
                            onChange={(e) => setFollow(row.id, { type: e.target.value as CustomFieldType })}
                          >
                            {CUSTOM_FIELD_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
                          </select>
                        </label>
                        <label className="flex items-center gap-1.5 pb-2 text-[12px] text-foreground-700">
                          <input
                            type="checkbox"
                            className="accent-primary-500"
                            checked={row.follow.required}
                            onChange={(e) => setFollow(row.id, { required: e.target.checked })}
                          />
                          Mandatory
                        </label>
                        {row.follow.type === 'dropdown' && (
                          <fieldset className="sm:col-span-3">
                            <legend className="text-[11.5px] font-medium text-foreground-600">Options of the follow-up dropdown</legend>
                            <ol className="mt-1 space-y-1.5">
                              {row.follow.options.map((option, j) => (
                                <li key={j} className="flex items-center gap-2">
                                  <input
                                    className={`${inputClass} flex-1`}
                                    value={option}
                                    placeholder={`Option ${j + 1}`}
                                    aria-label={`Follow-up option ${j + 1} for “${name}”`}
                                    aria-invalid={tried && followOptions(row).length === 0 ? true : undefined}
                                    onChange={(e) => setFollow(row.id, { options: row.follow.options.map((o, k) => (k === j ? e.target.value : o)) })}
                                  />
                                  <button
                                    type="button"
                                    className="flex h-8 w-8 shrink-0 cursor-pointer items-center justify-center rounded-lg text-foreground-400 hover:bg-background-100 hover:text-red-600 disabled:cursor-not-allowed disabled:opacity-40"
                                    aria-label={`Remove follow-up option ${j + 1} for “${name}”`}
                                    disabled={row.follow.options.length === 1}
                                    onClick={() => setFollow(row.id, { options: row.follow.options.filter((_, k) => k !== j) })}
                                  >
                                    <AppIcon className="ri-delete-bin-line" />
                                  </button>
                                </li>
                              ))}
                            </ol>
                            <button
                              type="button"
                              className="mt-1.5 inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-dashed border-foreground-200 px-2.5 py-1 text-[11.5px] font-medium text-primary-700 hover:bg-primary-50"
                              aria-label={`Add follow-up option for “${name}”`}
                              onClick={() => setFollow(row.id, { options: [...row.follow.options, ''] })}
                            >
                              <AppIcon className="ri-add-line" />Add option
                            </button>
                          </fieldset>
                        )}
                      </div>
                    )}
                  </li>
                );
              })}
            </ol>
            <button
              type="button"
              className="mt-2 inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-dashed border-foreground-200 px-3 py-1.5 text-[12px] font-medium text-primary-700 hover:bg-primary-50"
              onClick={() => setRows((rs) => [...rs, newRow('')])}
            >
              <AppIcon className="ri-add-line" />Add option
            </button>
            <p className="mt-1 text-[11px] text-foreground-400">
              A follow-up question is its own field, placed under this dropdown and asked only for that answer. Edit it later from the step like any other field — a follow-up dropdown can then have follow-ups of its own.
            </p>
          </fieldset>
        )}
        <label className="block text-[12px] font-medium text-foreground-600 sm:col-span-2" htmlFor={`${idPrefix}-help`}>
          Help text (optional)
          <input id={`${idPrefix}-help`} className={`${inputClass} mt-1`} value={draft.helpText} onChange={(e) => set({ helpText: e.target.value })} />
        </label>

        <div className="sm:col-span-2 rounded-lg border border-foreground-100 bg-background-50 p-3">
          <label className="flex items-center gap-2 text-[12px] font-medium text-foreground-700">
            <input
              type="checkbox"
              className="accent-primary-500"
              checked={Boolean(draft.condition)}
              disabled={sources.length === 0}
              onChange={(e) => set({ condition: e.target.checked ? { field: '', values: [] } : null })}
            />
            Only show this field for certain answers to a dropdown
          </label>
          {sources.length === 0 && <p className="mt-1 text-[11px] text-foreground-400">Add a dropdown field first to show this one depending on its answer.</p>}
          {draft.condition && (
            <div className="mt-3 space-y-2">
              <label className="block text-[12px] font-medium text-foreground-600" htmlFor={`${idPrefix}-source`}>
                When this dropdown…
                <select id={`${idPrefix}-source`} className={`${inputClass} mt-1 cursor-pointer`} value={draft.condition.field} onChange={(e) => chooseSource(e.target.value)}>
                  <option value="">Select a dropdown…</option>
                  {sources.map((s) => (
                    <option key={s.item.key} value={s.item.key}>{s.step.label} › {itemLabel(s.item)}</option>
                  ))}
                </select>
              </label>
              {source && (
                <fieldset>
                  <legend className="text-[12px] font-medium text-foreground-600">…is answered with any of</legend>
                  {source.options.length > 12 && (
                    <input
                      className={`${inputClass} mt-1`}
                      placeholder="Filter answers…"
                      aria-label="Filter answers"
                      value={optionFilter}
                      onChange={(e) => setOptionFilter(e.target.value)}
                    />
                  )}
                  <div className="mt-1 max-h-48 space-y-1 overflow-y-auto pr-1">
                    {shownOptions.map((o) => (
                      <label key={o} className="flex items-center gap-2 text-[12px] text-foreground-700">
                        <input type="checkbox" className="accent-primary-500" checked={draft.condition!.values.includes(o)} onChange={() => toggleValue(o)} />
                        {o}
                      </label>
                    ))}
                  </div>
                </fieldset>
              )}
            </div>
          )}
        </div>
      </div>

      {tried && errors.length > 0 && (
        <ul role="alert" className="mt-3 list-disc space-y-0.5 rounded-lg border border-red-200 bg-red-50 py-2 pl-7 pr-3 text-[12px] text-red-700">
          {errors.map((e) => <li key={e}>{e}</li>)}
        </ul>
      )}
      <div className="mt-4 flex flex-wrap justify-end gap-2">
        <button type="button" className={btnSecondary} onClick={onCancel}>Cancel</button>
        <button type="button" className={btnPrimary} onClick={submit}>
          <AppIcon className="ri-check-line" />{item ? 'Update field' : 'Add field'}
        </button>
      </div>
    </div>
  );
}
