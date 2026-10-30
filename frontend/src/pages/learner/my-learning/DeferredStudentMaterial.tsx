import { lazy, Suspense, type ComponentProps } from 'react';
import type { StudentMaterial as MaterialComponent } from './StudentMaterial';

const Material = lazy(() => import('./StudentMaterial').then(module => ({ default: module.StudentMaterial })));

/** Download the media players only when a learner opens an activity. */
export function DeferredStudentMaterial(props: ComponentProps<typeof MaterialComponent>) {
  return <Suspense fallback={<p role="status" className="p-5 text-sm text-foreground-500">Loading activity…</p>}>
    <Material {...props} />
  </Suspense>;
}
