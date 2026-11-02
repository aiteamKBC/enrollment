import { expect, it } from 'vitest';
import { defaultLayout, resolveLayout } from '@/pages/users/wizard/layout/resolve';
import { configuration } from './fixtures';
import { SUITABILITY_STEP } from '../wizardStep';

it('registers twelve default steps with suitability sixth and CV seventh', () => {
  const layout = defaultLayout();
  expect(layout.steps).toHaveLength(12);
  expect(layout.steps[5]).toMatchObject(SUITABILITY_STEP);
  expect(layout.steps[6].slug).toBe('cv-job');
});

it('adapts an old published layout before CV without mutating saved configuration or duplicating the step', () => {
  const old = { ...defaultLayout(), programmeSuitability: configuration };
  old.steps = old.steps.filter((s) => s.slug !== SUITABILITY_STEP.slug);
  const original = structuredClone(old);
  const resolved = resolveLayout(old);
  expect(old).toEqual(original);
  expect(resolved.steps[5]).toMatchObject(SUITABILITY_STEP);
  expect(resolved.programmeSuitability).toEqual(configuration);
  expect(resolveLayout(resolved)).toEqual(resolved);
});

it('keeps a mandatory assessment visible even if a published layout tries to hide it', () => {
  const saved = { ...defaultLayout(), programmeSuitability: configuration };
  saved.steps[5].hidden = true;
  expect(resolveLayout(saved).steps[5].hidden).toBe(false);
  expect(saved.steps[5].hidden).toBe(true);
});
