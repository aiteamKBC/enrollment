import { useMemo, useState } from 'react';
import { AppIcon } from '@/components/feature/AppIcon';
import { itemLabel } from '@/pages/apprenticeship-application/layout/resolve';
import { QUESTION_TYPES, type EligibilityItem, type EligibilityLayout, type EligibilityQuestionType } from '@/pages/apprenticeship-application/layout/types';
import { btnPrimary, btnSecondary, inputClass } from '../components/ui';
import { conditionSources, questionDraftFrom, type QuestionDraft } from './builderOps';
import { OptionsEditor } from './OptionsEditor';

/**
 * Add or edit one custom question: label, type, dropdown answers, help text,
 * mandatory, and an optional "only show when <question> is …" condition.
 *
 * A question that has been published already has a column of its type, so its
 * type is fixed here (the server refuses a change too).
 */
export function QuestionEditor({
  layout,
  item,
  onSave,
  onCancel,
}: {
  layout: EligibilityLayout;
  /** The question being edited; absent when adding. */
  item?: EligibilityItem;
  onSave: (draft: QuestionDraft) => void;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState<QuestionDraft>(() => {
    const initial = questionDraftFrom(item);
    return initial.type === 'dropdown' && initial.options.length === 0 ? { ...initial, options: ['', ''] } : initial;
  });
  const [tried, setTried] = useState(false);
  const published = Boolean(item?.column);
  const set = (patch: Partial<QuestionDraft>) => setDraft((d) => ({ ...d, ...patch }));

  const sources = useMemo(() => conditionSources(layout, item?.key), [layout, item?.key]);
  const source = draft.condition ? sources.find((s) => s.item.key === draft.condition!.field) : undefined;
  const isDropdown = draft.type === 'dropdown';
  const options = draft.options.map((o) => o.trim()).filter(Boolean);

  const errors: string[] = [];
  if (!draft.label.trim()) errors.push('Enter the question.');
  if (isDropdown && options.length === 0) errors.push('Add at least one answer to the dropdown.');
  if (isDropdown && new Set(options).size !== options.length) errors.push('Each answer must be different.');
  if (draft.condition && (!source || draft.condition.values.length === 0)) {
    errors.push('Choose the question and at least one answer that shows this one.');
  }

  const submit = () => {
    setTried(true);
    if (errors.length) return;
    onSave({
      ...draft,
      label: draft.label.trim(),
      helpText: draft.helpText.trim(),
      options: isDropdown ? options : [],
    });
  };

  const chooseType = (type: EligibilityQuestionType) =>
    set({ type, options: type === 'dropdown' && draft.options.length === 0 ? ['', ''] : draft.options });

  const toggleValue = (value: string) => {
    if (!draft.condition) return;
    const values = draft.condition.values.includes(value)
      ? draft.condition.values.filter((v) => v !== value)
      : [...draft.condition.values, value];
    set({ condition: { ...draft.condition, values } });
  };

  const idPrefix = `question-editor-${item?.key ?? 'new'}`;

  return (
    <div className="rounded-xl border border-primary-200 bg-primary-50/40 p-4" role="group" aria-label={item ? `Edit ${itemLabel(layout, item)}` : 'New question'}>
      <p className="mb-3 text-[13px] font-semibold text-foreground-900">{item ? 'Edit question' : 'New question'}</p>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block text-[12px] font-medium text-foreground-600 sm:col-span-2" htmlFor={`${idPrefix}-label`}>
          Question
          <input
            id={`${idPrefix}-label`}
            className={`${inputClass} mt-1`}
            value={draft.label}
            onChange={(e) => set({ label: e.target.value })}
            aria-invalid={tried && !draft.label.trim() ? true : undefined}
            placeholder="e.g. How many hours a week do you work?"
          />
        </label>
        <label className="block text-[12px] font-medium text-foreground-600" htmlFor={`${idPrefix}-type`}>
          Answer type
          <select
            id={`${idPrefix}-type`}
            className={`${inputClass} mt-1 cursor-pointer disabled:cursor-not-allowed disabled:opacity-60`}
            value={draft.type}
            disabled={published}
            onChange={(e) => chooseType(e.target.value as EligibilityQuestionType)}
          >
            {QUESTION_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
          </select>
          {published && <span className="mt-1 block text-[11px] font-normal text-foreground-400">Published questions keep their type — remove it and add a new question to change it.</span>}
        </label>
        <label className="flex items-center gap-2 self-end pb-2 text-[12px] font-medium text-foreground-700">
          <input type="checkbox" className="accent-primary-500" checked={draft.required} onChange={(e) => set({ required: e.target.checked })} />
          Mandatory — applicants must answer before moving on
        </label>
        {isDropdown && (
          <fieldset className="sm:col-span-2">
            <legend className="text-[12px] font-medium text-foreground-600">Dropdown answers</legend>
            <div className="mt-1">
              <OptionsEditor options={draft.options} onChange={(next) => set({ options: next })} name="Dropdown" showInvalid={tried} />
            </div>
          </fieldset>
        )}
        <label className="block text-[12px] font-medium text-foreground-600 sm:col-span-2" htmlFor={`${idPrefix}-help`}>
          Help text (optional)
          <input id={`${idPrefix}-help`} className={`${inputClass} mt-1`} value={draft.helpText} onChange={(e) => set({ helpText: e.target.value })} />
        </label>

        <div className="rounded-lg border border-foreground-100 bg-background-50 p-3 sm:col-span-2">
          <label className="flex items-center gap-2 text-[12px] font-medium text-foreground-700">
            <input
              type="checkbox"
              className="accent-primary-500"
              checked={Boolean(draft.condition)}
              disabled={sources.length === 0}
              onChange={(e) => set({ condition: e.target.checked ? { field: '', values: [] } : null })}
            />
            Only ask this question for certain answers to another question
          </label>
          {sources.length === 0 && <p className="mt-1 text-[11px] text-foreground-400">There is no question with set answers (a list, dropdown or yes/no) to depend on.</p>}
          {draft.condition && (
            <div className="mt-3 space-y-2">
              <label className="block text-[12px] font-medium text-foreground-600" htmlFor={`${idPrefix}-source`}>
                When this question…
                <select
                  id={`${idPrefix}-source`}
                  className={`${inputClass} mt-1 cursor-pointer`}
                  value={draft.condition.field}
                  onChange={(e) => set({ condition: e.target.value ? { field: e.target.value, values: [] } : { field: '', values: [] } })}
                >
                  <option value="">Select a question…</option>
                  {sources.map((s) => (
                    <option key={s.item.key} value={s.item.key}>{s.section.title} › {itemLabel(layout, s.item)}</option>
                  ))}
                </select>
              </label>
              {source && (
                <fieldset>
                  <legend className="text-[12px] font-medium text-foreground-600">…is answered with any of</legend>
                  <div className="mt-1 max-h-48 space-y-1 overflow-y-auto pr-1">
                    {source.answers.map((answer) => (
                      <label key={answer} className="flex items-center gap-2 text-[12px] text-foreground-700">
                        <input type="checkbox" className="accent-primary-500" checked={draft.condition!.values.includes(answer)} onChange={() => toggleValue(answer)} />
                        {answer}
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
          <AppIcon className="ri-check-line" />{item ? 'Update question' : 'Add question'}
        </button>
      </div>
    </div>
  );
}
