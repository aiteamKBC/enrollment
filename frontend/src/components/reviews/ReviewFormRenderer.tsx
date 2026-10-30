import { fieldAnswered, fieldAllowsRespondent, importedTableRows, computeVisibleRequiredFields, computeWritableFieldIds, computeVisibleWritableFieldIds, computeMissingRequiredFieldsForRole, computeMissingRequiredFields } from './ReviewFormRenderer.helpers';
import { AppIcon } from '@/components/feature/AppIcon';
import { cn } from '@/lib/cn';
import { flattenReviewFields, type ReviewFieldDefinition, type ReviewSectionDefinition } from '@/api/reviewInstances';
import type { ReactNode } from 'react';

/**
 * Renders whatever sections/fields a Curriculum Review template defines --
 * the ONE reusable form renderer every Coach Review (Monthly Coaching
 * Meeting, Progress Review, and any Review Curriculum creates later) opens
 * through. It must never hard-code a question list: everything it draws
 * comes from the `sections` prop, which is the Review instance's frozen
 * definition snapshot (see review_instance_form_definition on the backend).
 *
 * Visual language matches the existing hand-built Coach completion forms
 * (accordion sections, AppIcon, the same button-group answer controls) so a
 * Curriculum-driven Review looks native inside Coach rather than like a
 * second design system.
 */

export interface ReviewFieldErrorSet {
  /** field ids that are visible, required, and currently unanswered. */
  missingFieldIds: Set<string>;
}

interface ReviewFormRendererProps {
  sections: ReviewSectionDefinition[];
  answers: Record<string, unknown>;
  onAnswerChange: (fieldId: string, value: unknown) => void;
  errors?: ReviewFieldErrorSet;
  readOnly?: boolean;
  /** Per-field override of `readOnly`, e.g. so a Learner/Employer viewer can
   *  write exactly the fields their role was opted into (see
   *  computeWritableFieldIds) while every other field stays read-only. Takes
   *  priority over the blanket `readOnly` prop whenever it is supplied. */
  fieldReadOnly?: (field: ReviewFieldDefinition) => boolean;
  /** Optional non-coach respondent whose writable questions should be
   * labelled in the shared renderer. */
  respondentRole?: 'participant' | 'employer';
  /** Show a green indicator when a read-only viewer has saved content. */
  showSavedAnswerStatus?: boolean;
  openSectionId: string;
  onOpenSectionChange: (sectionId: string) => void;
  renderFieldAddon?: (field: ReviewFieldDefinition) => ReactNode;
  renderFieldInput?: (
    field: ReviewFieldDefinition,
    context: {
      value: unknown;
      onChange: (value: unknown) => void;
      readOnly: boolean;
      invalid: boolean;
    },
  ) => ReactNode | undefined;
  /** Optional read-only presentation; callers retain the original field controls. */
  renderSectionContent?: (section: ReviewSectionDefinition, renderFields: (fields: ReviewFieldDefinition[]) => ReactNode) => ReactNode;
  variant?: 'accordion' | 'steps';
}

function computeSectionRespondentSummary(
  section: ReviewSectionDefinition,
  answers: Record<string, unknown>,
  role: 'participant' | 'employer',
) {
  let count = 0;
  let required = 0;
  let missing = 0;
  let unanswered = 0;
  const walk = (fields: ReviewFieldDefinition[], visible: boolean) => {
    for (const field of fields) {
      const displayOnly = field.fieldType === 'title_description' || field.fieldType === 'action_button';
      const canRespond = visible && !displayOnly && fieldAllowsRespondent(field, role);
      if (canRespond) {
        count += 1;
        const answered = fieldAnswered(field, answers);
        if (!answered) unanswered += 1;
        if (field.required) {
          required += 1;
          if (!answered) missing += 1;
        }
      }
      if (field.fieldType === 'boolean_case_block') {
        const answer = answers[field.id];
        walk(field.yesFields || [], visible && answer === 'yes');
        walk(field.noFields || [], visible && answer === 'no');
      }
    }
  };
  if (section.enabled) walk(section.fields, true);
  // `unanswered` counts every field opened up to this respondent, not just the
  // required ones -- a section they were asked to fill in should not read as
  // "done" while an optional question of theirs is still blank.
  return { count, required, missing, unanswered };
}

function sectionRequiredFields(section: ReviewSectionDefinition) {
  return section.fields.filter((field) => field.required);
}

function sectionHasAnsweredFields(section: ReviewSectionDefinition, answers: Record<string, unknown>) {
  const walk = (fields: ReviewFieldDefinition[]): boolean => fields.some((field) => (
    fieldAnswered(field, answers)
    || (field.fieldType === 'boolean_case_block' && (
      walk(field.yesFields || []) || walk(field.noFields || [])
    ))
  ));
  return section.enabled && walk(section.fields);
}

export function ReviewFormRenderer({
  sections,
  answers,
  onAnswerChange,
  errors,
  readOnly,
  fieldReadOnly,
  respondentRole,
  showSavedAnswerStatus = false,
  openSectionId,
  onOpenSectionChange,
  renderFieldAddon,
  renderFieldInput,
  renderSectionContent,
  variant = 'accordion',
}: ReviewFormRendererProps) {
  const enabledSections = sections
    .filter((section) => section.enabled)
    .slice()
    .sort((a, b) => a.displayOrder - b.displayOrder);

  const renderFields = (fields: ReviewFieldDefinition[]) => fields.slice()
    .sort((a, b) => a.displayOrder - b.displayOrder)
    .map((field, index) => <ReviewFieldControl key={field.id} field={field} index={index}
      answers={answers} onAnswerChange={onAnswerChange} errors={errors} readOnly={readOnly}
      fieldReadOnly={fieldReadOnly} respondentRole={respondentRole}
      renderFieldAddon={renderFieldAddon} renderFieldInput={renderFieldInput} />);
  const renderContent = (section: ReviewSectionDefinition) => renderSectionContent
    ? renderSectionContent(section, renderFields) : renderFields(section.fields);

  if (variant === 'steps') {
    const activeIndex = Math.max(0, enabledSections.findIndex(section => section.id === openSectionId));
    const activeSection = enabledSections[activeIndex];
    const totalSteps = enabledSections.length;

    if (!activeSection) return null;

    return (
      <div className="grid items-start gap-5 lg:grid-cols-[17rem_minmax(0,1fr)]">
        <nav aria-label="Review steps" className="rounded-2xl border border-background-200 bg-white p-3 lg:sticky lg:top-4">
          <ol className="space-y-1.5">
            {enabledSections.map((section, sectionIndex) => {
              const selected = section.id === activeSection.id;
              const respondentSummary = respondentRole
                ? computeSectionRespondentSummary(section, answers, respondentRole)
                : null;
              const complete = respondentSummary
                ? respondentSummary.count > 0 && respondentSummary.unanswered === 0
                : computeMissingRequiredFields([section], answers).size === 0;
              const savedAnswer = showSavedAnswerStatus && sectionHasAnsweredFields(section, answers);
              const pending = Boolean(respondentSummary && respondentSummary.count > 0 && respondentSummary.unanswered > 0);
              const stepNumber = sectionIndex + 1;
              return (
                <li key={section.id}>
                  <button
                    type="button"
                    onClick={() => onOpenSectionChange(section.id)}
                    aria-current={selected ? 'step' : undefined}
                    className={cn(
                      'flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left transition',
                      selected ? 'bg-primary-50 text-primary-900 ring-1 ring-primary-200' : 'text-foreground-600 hover:bg-background-100',
                    )}
                  >
                    <span className={cn(
                      'flex h-8 w-8 shrink-0 items-center justify-center rounded-full border text-xs font-bold',
                      selected
                        ? 'border-primary-600 bg-primary-600 text-white'
                        : complete || savedAnswer
                          ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
                          : pending
                            ? 'border-amber-200 bg-amber-50 text-amber-700'
                            : 'border-background-300 bg-white text-foreground-500',
                    )}>
                      {(complete || savedAnswer) && !selected ? <AppIcon className="ri-check-line"></AppIcon> : stepNumber}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block text-[11px] font-bold uppercase tracking-[0.1em]">Step {stepNumber}</span>
                      <span className="mt-0.5 block text-xs font-semibold leading-4">{section.title}</span>
                      {respondentSummary?.count ? (
                        <span className="mt-1 block text-[11px] font-medium text-primary-700">
                          {respondentSummary.count} question{respondentSummary.count === 1 ? '' : 's'} for you
                          {respondentSummary.required ? ` · ${respondentSummary.required} required` : ''}
                        </span>
                      ) : null}
                    </span>
                  </button>
                </li>
              );
            })}
          </ol>
        </nav>

        <section id={`review-section-${activeSection.id}`} className="overflow-hidden rounded-2xl border border-primary-200 bg-white shadow-sm" aria-labelledby={`review-step-${activeSection.id}`}>
          <header className="border-b border-primary-100 bg-primary-50/70 px-5 py-4 sm:px-6">
            <div className="flex items-start gap-3">
              <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary-600 text-sm font-bold text-white">
                {activeIndex + 1}
              </span>
              <div className="min-w-0">
                <p className="text-[11px] font-bold uppercase tracking-[0.14em] text-primary-600">
                  Step {activeIndex + 1} of {totalSteps}
                </p>
                <h2 id={`review-step-${activeSection.id}`} className="mt-1 text-base font-bold text-foreground-950">
                  {activeSection.title}
                </h2>
                {activeSection.estimatedMinutes ? <p className="mt-1 text-xs text-foreground-500">About {activeSection.estimatedMinutes} minutes</p> : null}
              </div>
            </div>
          </header>

          <div className="space-y-3 p-4 sm:p-6">
            {renderContent(activeSection)}
          </div>

          {enabledSections.length > 1 ? (
            <footer className="flex items-center justify-between gap-3 border-t border-background-200 bg-background-50 px-4 py-3 sm:px-6">
              <button
                type="button"
                onClick={() => onOpenSectionChange(enabledSections[activeIndex - 1].id)}
                disabled={activeIndex === 0}
                className="inline-flex h-9 items-center gap-1.5 rounded-lg px-3 text-xs font-semibold text-foreground-600 transition hover:bg-background-100 disabled:invisible"
              >
                <AppIcon className="ri-arrow-left-line"></AppIcon>Previous
              </button>
              {activeIndex < enabledSections.length - 1 ? (
                <button
                  type="button"
                  onClick={() => onOpenSectionChange(enabledSections[activeIndex + 1].id)}
                  className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-primary-600 px-4 text-xs font-bold text-white transition hover:bg-primary-700"
                >
                  Next step<AppIcon className="ri-arrow-right-line"></AppIcon>
                </button>
              ) : (
                <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-emerald-700">
                  <AppIcon className="ri-checkbox-circle-line"></AppIcon>Final form step
                </span>
              )}
            </footer>
          ) : null}
        </section>
      </div>
    );
  }

  return (
    <>
      {enabledSections.map((section, sectionIndex) => {
        const open = openSectionId === section.id;
        const respondentSummary = respondentRole
          ? computeSectionRespondentSummary(section, answers, respondentRole)
          : null;
        const requiredFields = sectionRequiredFields(section);
        const complete = respondentSummary
          ? respondentSummary.count > 0 && respondentSummary.unanswered === 0
          : requiredFields.every((field) => fieldAnswered(field, answers));
        const savedAnswer = showSavedAnswerStatus && sectionHasAnsweredFields(section, answers);
        const pending = Boolean(respondentSummary && respondentSummary.count > 0 && respondentSummary.unanswered > 0);
        return (
          <section id={`review-section-${section.id}`} key={section.id} className={cn('overflow-hidden rounded-2xl border bg-background-50 transition-all', open ? 'border-primary-300 shadow-sm' : respondentSummary?.count ? 'border-primary-300 bg-primary-50/30' : 'border-background-200')}>
            <button
              type="button"
              onClick={() => onOpenSectionChange(open ? '' : section.id)}
              aria-expanded={open}
              aria-label={`${section.title}${respondentSummary?.count ? `, ${respondentSummary.count} question${respondentSummary.count === 1 ? '' : 's'} for you` : ''}`}
              className={cn('flex w-full items-center gap-3 p-4 text-left transition-colors sm:px-5', respondentSummary?.count ? 'hover:bg-primary-50/80' : 'hover:bg-background-100')}
            >
              <span className={cn('relative flex h-10 w-10 shrink-0 items-center justify-center rounded-lg', open ? 'bg-primary-600 text-white' : respondentSummary?.count ? 'bg-primary-100 text-primary-700' : 'bg-primary-50 text-primary-700')}>
                <AppIcon className="ri-file-list-3-line"></AppIcon>
                <span className="absolute -right-1.5 -top-1.5 flex h-5 min-w-5 items-center justify-center rounded-full border-2 border-white bg-primary-900 px-1 text-[12px] font-bold text-white">{sectionIndex + 1}</span>
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-[12px] font-bold uppercase tracking-[0.12em] text-foreground-400">Section {sectionIndex + 1} of {enabledSections.length}</span>
                <span className="mt-0.5 block text-sm font-bold text-foreground-900">{section.title}</span>
                {section.estimatedMinutes ? <span className="mt-1 hidden text-[12px] text-foreground-400 sm:block">{section.estimatedMinutes} minutes</span> : null}
                {respondentSummary?.count ? (
                  <span className="mt-1 inline-flex items-center gap-1 rounded-full border border-primary-200 bg-white px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-primary-700">
                    <AppIcon className="ri-cursor-line"></AppIcon>
                    {respondentSummary.count} question{respondentSummary.count === 1 ? '' : 's'} for you · Open
                    {respondentSummary.required ? ` · ${respondentSummary.required} required` : ''}
                  </span>
                ) : null}
              </span>
              {complete || savedAnswer ? (
                <AppIcon
                  aria-label={savedAnswer && !complete ? 'Section has saved answers' : 'Section complete'}
                  className="ri-checkbox-circle-fill text-lg text-emerald-500"
                ></AppIcon>
              ) : pending ? (
                <AppIcon className="ri-time-line text-lg text-amber-500"></AppIcon>
              ) : null}
              <span className={cn('flex h-8 w-8 items-center justify-center rounded-full bg-background-100 text-foreground-500 transition-transform', open && 'rotate-180')}>
                <AppIcon className="ri-arrow-down-s-line"></AppIcon>
              </span>
            </button>

            {open ? (
              <div className="space-y-3 border-t border-primary-100 bg-white p-4 sm:p-5">
                {renderContent(section)}
              </div>
            ) : null}
          </section>
        );
      })}
    </>
  );
}

function ReviewFieldControl({
  field, index, answers, onAnswerChange, errors, readOnly, fieldReadOnly, respondentRole, renderFieldAddon, renderFieldInput,
}: {
  field: ReviewFieldDefinition;
  index: number;
  answers: Record<string, unknown>;
  onAnswerChange: (fieldId: string, value: unknown) => void;
  errors?: ReviewFieldErrorSet;
  readOnly?: boolean;
  fieldReadOnly?: (field: ReviewFieldDefinition) => boolean;
  respondentRole?: 'participant' | 'employer';
  renderFieldAddon?: (field: ReviewFieldDefinition) => ReactNode;
  renderFieldInput?: ReviewFormRendererProps['renderFieldInput'];
}) {
  const value = answers[field.id];
  const invalid = Boolean(errors?.missingFieldIds.has(field.id));
  const isMeetingSummary = field.configuration?.semanticKey === 'meeting_summary' && field.configuration?.migrated !== true;
  const effectiveReadOnly = fieldReadOnly ? fieldReadOnly(field) : Boolean(readOnly);
  const canRespond = respondentRole ? fieldAllowsRespondent(field, respondentRole) : false;
  const respondentLabel = respondentRole === 'employer' ? 'Employer response' : 'Your response';

  if (field.fieldType === 'title_description') {
    const description = String(field.configuration?.description || '');
    const tableRows = importedTableRows(field.configuration);
    // Imported Aptem records arrive with a generic "Imported text" placeholder
    // as the title and carry the real prompt/content in `description`. Showing
    // that placeholder as the heading turns the actual question into a mystery
    // label (e.g. "Imported text" with "Meeting Summary" beneath it), so render
    // the real content instead of the placeholder for these imported items.
    const isGenericImportedText = field.configuration?.imported === true
      && !tableRows
      && field.title.trim().toLocaleLowerCase() === 'imported text';
    return (
      <div className="rounded-lg border border-background-200 bg-background-100/70 px-4 py-3">
        <div className="flex items-start gap-3">
          <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary-100 text-[12px] font-bold text-primary-700">{index + 1}</span>
          <div className="min-w-0 flex-1">
            {isGenericImportedText ? (
              description
                ? <p className="whitespace-pre-wrap text-sm leading-5 text-foreground-700">{description}</p>
                : <p className="text-sm font-semibold leading-5 text-foreground-900">Imported note</p>
            ) : (<>
            <p className="text-sm font-semibold leading-5 text-foreground-900">{field.title}</p>
            {tableRows ? <div className="mt-3 max-w-full overflow-x-auto rounded-lg border border-background-200 bg-background-50">
              <table className="min-w-full border-collapse text-left text-sm">
                <thead className="bg-primary-50 text-foreground-900"><tr>{tableRows[0].map((cell, column) =>
                  <th key={column} scope="col" className="whitespace-nowrap border-b border-background-200 px-3 py-2 font-semibold">{cell}</th>)}</tr></thead>
                <tbody>{tableRows.slice(1).map((row, rowIndex) => <tr key={rowIndex} className="border-b border-background-100 last:border-b-0">
                  {row.map((cell, column) => <td key={column} className="min-w-36 align-top px-3 py-2 leading-5 text-foreground-600">{cell}</td>)}
                </tr>)}</tbody>
              </table>
            </div> : description ? <p className="mt-1 whitespace-pre-wrap text-sm leading-5 text-foreground-500">{description}</p> : null}
            </>)}
          </div>
        </div>
      </div>
    );
  }

  if (field.fieldType === 'action_button') {
    return (
      <div className="rounded-2xl border border-background-200 bg-background-50 p-4">
        <button type="button" disabled={effectiveReadOnly} className="inline-flex h-10 items-center gap-2 rounded-lg bg-primary-600 px-4 text-xs font-bold text-white shadow-sm transition hover:bg-primary-700 disabled:opacity-60">
          <AppIcon className="ri-flashlight-line"></AppIcon>{field.title}
        </button>
      </div>
    );
  }

  return (
    <div className={cn('rounded-2xl border p-4 transition focus-within:border-primary-300 focus-within:shadow-sm', canRespond ? 'border-primary-300 bg-primary-50/30' : 'bg-background-50', invalid ? 'border-red-300' : canRespond ? 'border-primary-300' : 'border-background-200')}>
      <label className="mb-2.5 block !text-base !font-semibold !leading-5 !text-foreground-900">
        <span className="mr-2 inline-flex h-6 min-w-6 items-center justify-center rounded-full bg-primary-100 px-1 text-xs font-bold text-primary-700">{index + 1}</span>
        {isMeetingSummary ? 'Meeting Summary' : field.title}
        {field.required ? <span className="ml-1 text-red-500">*</span> : null}
        {canRespond ? (
          <span className="ml-2 inline-flex items-center gap-1 rounded-full border border-primary-200 bg-primary-50 px-2 py-0.5 align-middle text-[10px] font-bold uppercase tracking-wide text-primary-700">
            <AppIcon className="ri-edit-2-line"></AppIcon>{respondentLabel}
            {field.required ? <span className="text-red-700">Required</span> : null}
          </span>
        ) : null}
      </label>

      {renderFieldAddon?.(field)}
      {renderFieldInput?.(field, {
        value,
        onChange: (next) => onAnswerChange(field.id, next),
        readOnly: effectiveReadOnly,
        invalid,
      }) ?? (
        <ReviewFieldInput field={field} value={value} onChange={(next) => onAnswerChange(field.id, next)} readOnly={effectiveReadOnly} />
      )}

      {field.fieldType === 'boolean_case_block' ? (
        <div className="mt-3 space-y-3 border-l-2 border-primary-100 pl-4">
          {value === 'yes' ? (field.yesFields || []).map((child, childIndex) => (
            <ReviewFieldControl key={child.id} field={child} index={childIndex} answers={answers} onAnswerChange={onAnswerChange} errors={errors} readOnly={readOnly} fieldReadOnly={fieldReadOnly} respondentRole={respondentRole} renderFieldAddon={renderFieldAddon} renderFieldInput={renderFieldInput} />
          )) : null}
          {value === 'no' ? (field.noFields || []).map((child, childIndex) => (
            <ReviewFieldControl key={child.id} field={child} index={childIndex} answers={answers} onAnswerChange={onAnswerChange} errors={errors} readOnly={readOnly} fieldReadOnly={fieldReadOnly} respondentRole={respondentRole} renderFieldAddon={renderFieldAddon} renderFieldInput={renderFieldInput} />
          )) : null}
        </div>
      ) : null}
    </div>
  );
}

function ReviewFieldInput({
  field, value, onChange, readOnly,
}: {
  field: ReviewFieldDefinition;
  value: unknown;
  onChange: (value: unknown) => void;
  readOnly?: boolean;
}) {
  const stringValue = typeof value === 'string' ? value : '';

  switch (field.fieldType) {
    case 'text':
    case 'text_multiline':
      return (
        <textarea
          aria-label={field.configuration?.migrated === true && field.configuration?.semanticKey === 'meeting_summary' ? field.title : undefined}
          value={stringValue}
          onChange={(e) => onChange(e.target.value)}
          rows={3}
          maxLength={field.configuration?.semanticKey === 'meeting_summary' && field.configuration?.migrated !== true ? undefined : 4000}
          disabled={readOnly}
          placeholder={String(field.configuration?.placeholder || '')}
          className="w-full resize-y rounded-lg border border-background-300 bg-white px-3.5 py-3 text-sm text-foreground-800 outline-none transition placeholder:text-foreground-300 focus:border-primary-400 focus:ring-2 focus:ring-primary-200 disabled:bg-background-100"
        />
      );
    case 'numeric':
      return (
        <input
          type="number"
          value={stringValue}
          onChange={(e) => onChange(e.target.value)}
          disabled={readOnly}
          className="h-11 w-full max-w-sm rounded-lg border border-background-300 bg-white px-3.5 text-sm font-semibold text-foreground-800 outline-none transition focus:border-primary-400 focus:ring-2 focus:ring-primary-200"
        />
      );
    case 'email':
      return (
        <input
          type="email"
          value={stringValue}
          onChange={(e) => onChange(e.target.value)}
          disabled={readOnly}
          className="h-11 w-full max-w-sm rounded-lg border border-background-300 bg-white px-3.5 text-sm text-foreground-800 outline-none transition focus:border-primary-400 focus:ring-2 focus:ring-primary-200"
        />
      );
    case 'phone':
      return (
        <input
          type="tel"
          value={stringValue}
          onChange={(e) => onChange(e.target.value)}
          disabled={readOnly}
          className="h-11 w-full max-w-sm rounded-lg border border-background-300 bg-white px-3.5 text-sm text-foreground-800 outline-none transition focus:border-primary-400 focus:ring-2 focus:ring-primary-200"
        />
      );
    case 'postcode_address':
      return (
        <textarea
          value={stringValue}
          onChange={(e) => onChange(e.target.value)}
          rows={2}
          disabled={readOnly}
          placeholder="Address / postcode"
          className="w-full resize-y rounded-lg border border-background-300 bg-white px-3.5 py-3 text-sm text-foreground-800 outline-none transition focus:border-primary-400 focus:ring-2 focus:ring-primary-200"
        />
      );
    case 'date':
      return (
        <input
          type="date"
          value={stringValue}
          onChange={(e) => onChange(e.target.value)}
          disabled={readOnly}
          className="h-11 w-full max-w-sm rounded-lg border border-background-300 bg-white px-3.5 text-sm font-semibold text-foreground-800 outline-none transition focus:border-primary-400 focus:ring-2 focus:ring-primary-200"
        />
      );
    case 'boolean':
    case 'boolean_case_block':
      return (
        <div className="grid max-w-sm grid-cols-2 gap-2">
          {(['yes', 'no'] as const).map((option) => (
            <button
              key={option}
              type="button"
              disabled={readOnly}
              onClick={() => onChange(option)}
              className={cn(
                'flex h-11 items-center justify-center gap-2 rounded-lg border !text-sm !font-semibold !capitalize !leading-5 transition',
                value === option
                  ? (option === 'yes' ? 'border-emerald-500 bg-emerald-500 text-white shadow-sm' : 'border-foreground-700 bg-foreground-800 text-white shadow-sm')
                  : 'border-background-300 bg-white text-foreground-600 hover:border-primary-300 hover:bg-primary-50',
              )}
            >
              <AppIcon className={option === 'yes' ? 'ri-check-line' : 'ri-close-line'}></AppIcon>{option}
            </button>
          ))}
        </div>
      );
    case 'list_item': {
      const options = Array.isArray(field.configuration?.options) ? (field.configuration!.options as string[]) : [];
      return (
        <div className="grid gap-2 sm:grid-cols-2">
          {options.map((option) => (
            <button
              key={option}
              type="button"
              disabled={readOnly}
              onClick={() => onChange(option)}
              className={cn(
                'min-h-10 rounded-lg border px-3 py-2 text-left !text-sm !font-medium !leading-5 transition',
                value === option ? 'border-primary-600 bg-primary-600 text-white shadow-sm' : 'border-background-300 bg-white text-foreground-600 hover:border-primary-300 hover:bg-primary-50',
              )}
            >
              <AppIcon className={cn('mr-2', value === option ? 'ri-checkbox-circle-fill' : 'ri-checkbox-blank-circle-line')}></AppIcon>{option}
            </button>
          ))}
        </div>
      );
    }
    default:
      return null;
  }
}
