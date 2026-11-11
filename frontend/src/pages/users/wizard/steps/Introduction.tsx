import { StepHeading } from './fields';
import { RichText } from '../layout/RichText';
import { useText } from '../layout/textsContext';
import { WelcomeContent } from '@/features/learner-enrolment/EnrolmentVisuals';
import { useLearnerEnrolmentVisuals } from '@/features/learner-enrolment/enrolmentPresentation';

const sectionHeading = 'font-heading text-[15px] font-semibold text-primary-600 sm:text-base pt-3 first:pt-0';

/** The Welcome page. Its wording is edited in the wizard builder (block.introduction). */
export default function Introduction() {
  const t = useText();
  const learnerVisuals = useLearnerEnrolmentVisuals();
  if (learnerVisuals) return <WelcomeContent title={t('block.introduction.title')} source={t('block.introduction.body')} />;
  return (
    <div>
      <StepHeading title={t('block.introduction.title')} />
      <RichText
        source={t('block.introduction.body')}
        classes={{
          container: 'max-w-3xl space-y-3 text-sm leading-relaxed text-foreground-700 sm:text-[14px]',
          heading: sectionHeading,
          list: 'list-disc space-y-1 pl-6',
        }}
      />
    </div>
  );
}
