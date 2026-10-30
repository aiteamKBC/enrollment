import { StepErrorsContext, findEntry, useMissing, useMissingCheck, useShowStepErrors, lowerWords, requiredMessage, missingMessage, type StepErrors, type Verb } from './stepErrors.helpers';
export type { StepErrors } from './stepErrors.helpers';
import { createContext, useContext } from 'react';

/** Red outline for an outstanding box; appended to the box's own classes. */
export const invalidClass = ' !border-red-500 focus:!border-red-500 focus:!ring-red-100';

/** The note under an outstanding box. */
export function FieldError({ message }: { message: string }) {
  return (
    <p className="mt-1.5 inline-block rounded-md bg-red-50 px-2 py-0.5 text-[12px] text-red-600">{message}</p>
  );
}
