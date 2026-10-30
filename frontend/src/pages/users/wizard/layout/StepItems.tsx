import { Fragment, type ComponentType, type ReactNode } from 'react';
import { useWizard } from "../WizardContext.helpers";
import Introduction from '../steps/Introduction';
import BeforeYouBegin from '../steps/BeforeYouBegin';
import Plr from '../steps/Plr';
import SkillsRadar from '../steps/SkillsRadar';
import Inclusion from '../steps/Inclusion';
import Policies from '../steps/Policies';
import NextSteps from '../steps/NextSteps';
import { CustomField } from './CustomField';
import { BUILTIN_ITEMS, BUILTIN_STEP_BY_SLUG } from './registry';
import { indexItems, isRequired, isShown } from './resolve';
import { useLayout } from './useLayout';
import type { LayoutItem } from './types';

/**
 * How a step draws one of its own built-in items. `required` is the layout's
 * say (so a field made optional loses its star); a section heading also gets
 * the items that follow it as `children`, to wrap them in its fieldset.
 */
export type ItemRenderer = (ctx: { required: boolean; children?: ReactNode }) => ReactNode;

/** Self-contained parts that can sit on any step. */
const BLOCKS: Record<string, ComponentType> = {
  'block.introduction': Introduction,
  'block.beforeYouBegin': BeforeYouBegin,
  'block.plr': Plr,
  'block.skillsRadar': SkillsRadar,
  'block.inclusion': Inclusion,
  'block.policies': Policies,
  'block.nextSteps': NextSteps,
};

/**
 * A step's items, in the published layout's order: removed items and
 * unmet conditions skipped, custom fields and blocks placed where the layout
 * puts them. Items after a section heading are grouped under it.
 */
export function StepItems({ slug, renderers = {} }: { slug: string; renderers?: Record<string, ItemRenderer> }) {
  const { draft } = useWizard();
  const layout = useLayout();
  const step = layout.steps.find((s) => s.slug === slug);
  if (!step) return null;
  const index = indexItems(layout);
  const grouped = Boolean(BUILTIN_STEP_BY_SLUG[slug]?.sectionStyle);

  const renderItem = (item: LayoutItem): ReactNode => {
    if (!item.builtin) return <CustomField key={item.key} item={item} />;
    const own = renderers[item.key];
    if (own) return <Fragment key={item.key}>{own({ required: isRequired(item) })}</Fragment>;
    const Block = BLOCKS[item.key];
    return Block ? <Block key={item.key} /> : null;
  };

  // Split into runs: whatever precedes the first heading, then one run per heading.
  const runs: { heading: LayoutItem | null; items: LayoutItem[] }[] = [{ heading: null, items: [] }];
  for (const item of step.items) {
    if (!isShown(item, draft, index)) continue;
    if (grouped && item.builtin && BUILTIN_ITEMS[item.key]?.kind === 'heading' && renderers[item.key]) {
      runs.push({ heading: item, items: [] });
    } else {
      runs[runs.length - 1].items.push(item);
    }
  }

  return (
    <>
      {runs.map((run, i) =>
        run.heading ? (
          <Fragment key={run.heading.key}>
            {renderers[run.heading.key]({ required: false, children: run.items.map(renderItem) })}
          </Fragment>
        ) : (
          <Fragment key={`run-${i}`}>{run.items.map(renderItem)}</Fragment>
        )
      )}
    </>
  );
}
