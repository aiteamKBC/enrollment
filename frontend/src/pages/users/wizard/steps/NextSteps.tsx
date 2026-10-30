import { StepHeading } from './fields';
import { RichText } from '../layout/RichText';
import { useText } from '../layout/textsContext';

/**
 * The What Happens Now? page — including the link to Student Support's
 * Microsoft Bookings page for the compliance meeting. Its wording is edited in
 * the wizard builder (block.nextSteps).
 */
export default function NextSteps() {
  const t = useText();
  return (
    <div>
      <StepHeading title={t('block.nextSteps.title')} />
      <RichText
        source={t('block.nextSteps.body')}
        classes={{
          container: 'max-w-3xl space-y-3 text-[14px] leading-relaxed text-foreground-700',
          heading: 'font-heading text-[16px] font-semibold text-primary-700 pt-3 first:pt-0',
          list: 'list-disc space-y-1 pl-5',
          link: 'font-medium text-primary-600 hover:underline',
        }}
      />
    </div>
  );
}
