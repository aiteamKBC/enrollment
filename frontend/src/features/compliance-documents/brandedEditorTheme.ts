import type { CSSProperties } from 'react';
import { BRANDED_PURPLE, CURRENT_BRAND_ID, isBrandedPresentation } from './brandedPurpleTheme';
import { PDF_HEADER } from './documentHeader';
import type { DocumentTemplate } from './types';

// Existing design IDs stay intact. The saved presentation discriminates historical
// learner drafts; the global editor prepares a current, unpublished editable copy.
export function editorBrand(content: DocumentTemplate, presentation = CURRENT_BRAND_ID) {
  return (!content.designTheme || content.designTheme === 'purple-reference-v2') && isBrandedPresentation(presentation) ? presentation : undefined;
}
export function brandedEditorVariables(): CSSProperties {
  const theme = BRANDED_PURPLE;
  return { ...Object.fromEntries(['heading', 'secondary', 'section', 'label', 'border', 'text', 'muted', 'white', 'accent'].map(key =>
    [`--document-${key}`, `rgb(${theme[key as 'heading'].join(',')})`])),
    ...Object.fromEntries(Object.entries(theme.font).map(([key, value]) => [`--document-font-${key}`, `${value}pt`])),
    '--document-table-padding': `${theme.tablePadding}mm`, '--document-line-height': theme.lineHeight,
  } as CSSProperties;
}
export function editorMargins(content: DocumentTemplate, brand: string | undefined, page: number) {
  const configured = content.pageLayout?.margins;
  const defaults = BRANDED_PURPLE.margins;
  return { ...(configured || defaults), top: Math.max(configured?.top || 0, brand === CURRENT_BRAND_ID ? PDF_HEADER.contentTop : page === 1 ? 29 : 21), bottom: Math.max(configured?.bottom || 0, defaults.bottom) };
}

// The same section categories and white glyph geometry used by brandedPurplePdf.
// SVGs are small decorative backgrounds, not DOM children of editable headings.
function sectionIcon(title: string) {
  const paths = /health|safety|disab/i.test(title)
    ? '<path d="M4 6.8C1 4.8 .3 2.8 1.9 1.8C2.8 1.2 3.7 1.8 4 2.5C4.7 .8 6.7 1.3 7 2.7C7.5 4.3 5.4 6 4 6.8Z M.8 4H2.5L3.2 3L4.1 5.3L4.8 4H7.3"/>'
    : /education|training|qualification|learning|rpl|prior/i.test(title)
    ? '<path d="M.2 2.5L4 .8L7.8 2.5L4 4.3Z M1.4 3.2V5.8L4 6.8L6.6 5.8V3.2 M7.8 2.5V5.2"/>'
    : /employ|work|delivery|responsibil|provider/i.test(title)
    ? '<rect x=".6" y="2.5" width="6.8" height="4.8" rx=".6"/><rect x="2.5" y="1" width="3" height="2" rx=".4"/><path d="M.6 4.5H7.4 M4 4V5.3"/>'
    : /learner|personal|apprentice|contact|signature/i.test(title)
    ? '<circle cx="4" cy="2.5" r="1.45"/><rect x="1.4" y="4.6" width="5.2" height="2.6" rx=".8"/>'
    : '<rect x="1.5" y=".7" width="5.1" height="6.6" rx=".4"/><path d="M2.5 2.5H5.5 M2.5 4H5.5 M2.5 5.5H4.7"/>';
  return `url("data:image/svg+xml,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 8 8" fill="none" stroke="white" stroke-width=".42">${paths}</svg>`)}")`;
}
export function decorateBrandedContent(surface: HTMLElement) {
  surface.querySelectorAll<HTMLElement>('h2,h3,h1.document-section-title,.document-signature > strong').forEach(heading => {
    heading.dataset.brandedSection = 'true';
    heading.style.setProperty('--document-section-icon', sectionIcon(heading.textContent || ''));
  });
}
