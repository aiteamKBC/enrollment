import { expect, it } from 'vitest';
import { defaultLayout, layoutForLearner, resolveLayout, visibleSteps } from '@/pages/users/wizard/layout/resolve';
import { configuration } from './fixtures';
import { SUITABILITY_STEP } from '../wizardStep';

it('uses the published visibility for the thirteen-step apprenticeship journey', () => {
  const layout = defaultLayout();
  expect(layout.steps).toHaveLength(13);
  expect(layout.steps[2].slug).toBe('personal-details');
  expect(layout.steps[5]).toMatchObject(SUITABILITY_STEP);
  expect(layout.steps[6].slug).toBe('cv-job');
  const apprentice = visibleSteps(layoutForLearner(layout, false));
  expect(apprentice).toHaveLength(13);
  expect(apprentice.map(step => step.slug)).toContain('personal-details');
  expect(apprentice[5]).toMatchObject(SUITABILITY_STEP);
  expect(apprentice[6].slug).toBe('cv-job');
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
