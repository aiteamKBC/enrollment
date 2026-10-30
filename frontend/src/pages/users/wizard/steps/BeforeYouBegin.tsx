import { StepHeading } from './fields';
import { RichText } from '../layout/RichText';
import { useText } from '../layout/textsContext';

const sectionHeading = 'font-heading text-[15px] font-semibold text-primary-600 sm:text-base pt-3 first:pt-0';

/** The Before You Begin page. Its wording is edited in the wizard builder (block.beforeYouBegin). */
export default function BeforeYouBegin() {
  const t = useText();
  return (
    <div>
      <StepHeading title={t('block.beforeYouBegin.title')} />
      <RichText
        source={t('block.beforeYouBegin.body')}
        classes={{
          container: 'max-w-3xl space-y-3 text-sm leading-relaxed text-foreground-700 sm:text-[14px]',
          heading: sectionHeading,
          list: 'list-disc space-y-1 pl-6',
          link: 'break-all text-primary-600 hover:underline',
        }}
      />
    </div>
  );
}
