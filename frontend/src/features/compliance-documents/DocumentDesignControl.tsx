import { inputClass } from '@/pages/users/components/ui';
import type { DocumentTemplate } from './types';
import { isBrandedPresentation } from './brandedPurpleTheme';

export function DocumentDesignControl({ content, presentationTheme, onChange }: { content: DocumentTemplate; presentationTheme?: string; onChange: (content: DocumentTemplate) => void }) {
  return <label className="text-sm">Document design<select className={`${inputClass} mt-1`} value={content.designTheme || (isBrandedPresentation(presentationTheme) ? 'purple-reference-v2' : 'classic')} onChange={e => onChange({ ...content, designTheme: e.target.value as DocumentTemplate['designTheme'] })}>
    <option value="purple-reference-v2">KBC &amp; DfE — Purple Branded Design</option><option value="lavender-v1">Light purple — previous design</option><option value="classic">Original layout</option>
  </select></label>;
}
