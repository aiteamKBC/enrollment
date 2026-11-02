import { StepHeading } from '@/pages/users/wizard/steps/fields';
import { StepItems } from '@/pages/users/wizard/layout/StepItems';
import { useStepTitle } from '@/pages/users/wizard/layout/useLayout';
import { CvSuitabilityPanel } from './CvSuitabilityPanel';
import { SUITABILITY_STEP } from './wizardStep';
import { ProgrammeModulesCard } from './modules/ProgrammeModulesCard';

export default function ProgrammeSuitabilityStep() {
  const title = useStepTitle(SUITABILITY_STEP.slug, SUITABILITY_STEP.label);
  return <div>
    <StepHeading title={title} />
    <div className="max-w-3xl">
      <CvSuitabilityPanel />
    </div>
    <ProgrammeModulesCard />
    <div className="max-w-3xl">
      <StepItems slug={SUITABILITY_STEP.slug} />
    </div>
  </div>;
}
