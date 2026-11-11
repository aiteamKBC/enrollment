import type { CSSProperties } from 'react';
import type { BlockStyle, DocumentBlock, DocumentTemplate } from './types';
import { REFERENCE_PURPLE, referenceDecorationSvg } from './purpleReferenceTheme';

// Versioned presentation defaults. Old snapshots without this opt-in keep their
// original renderer/styles. Change the theme ID for a future visual revision.
export const LAVENDER = {
  heading: [84, 32, 166], secondary: [117, 72, 191], section: [243, 238, 255],
  label: [248, 245, 255], border: [217, 204, 244], text: [37, 34, 56], muted: [102, 97, 118], white: [255, 255, 255],
  font: { title: 18, section: 12, subheading: 10.5, body: 9.5, table: 9, caption: 8 },
  lineHeight: 1.45, padding: 3, tablePadding: 2.6, margins: { top: 22, right: 18, bottom: 20, left: 18 },
} as const;
export const isReferencePurple = (template: DocumentTemplate) => template.designTheme === 'purple-reference-v2';
export const isLavender = (template: DocumentTemplate) => template.designTheme === 'lavender-v1' || isReferencePurple(template);
export const documentPalette = (template?: DocumentTemplate) => template && isReferencePurple(template) ? REFERENCE_PURPLE : LAVENDER;
export interface DocumentPalette {
  heading: readonly [number,number,number]; secondary: readonly [number,number,number]; section: readonly [number,number,number];
  label: readonly [number,number,number]; border: readonly [number,number,number]; text: readonly [number,number,number]; muted: readonly [number,number,number]; white: readonly [number,number,number];
  font: { title:number;section:number;subheading:number;body:number;table:number;caption:number }; lineHeight:number;padding:number;tablePadding:number;
}
export function designDraft(template: DocumentTemplate): DocumentTemplate {
  return template.designTheme && template.designTheme !== 'lavender-v1' ? template : { ...template, designTheme: 'purple-reference-v2' };
}
export function themeVariables(template?: DocumentTemplate): CSSProperties {
  const palette=documentPalette(template);
  return { ...Object.fromEntries(['heading', 'secondary', 'section', 'label', 'border', 'text', 'muted', 'white'].map((key) =>
    [`--document-${key}`, `rgb(${palette[key as 'heading'].join(',')})`])),
    ...Object.fromEntries(Object.entries(palette.font).map(([key,value])=>[`--document-font-${key}`,`${value}pt`])),
    '--document-table-padding': `${palette.tablePadding}mm`, '--document-line-height':palette.lineHeight,
    ...(template && isReferencePurple(template) ? { '--document-accent':'rgb(143,26,235)', '--document-top-wave':`url("data:image/svg+xml,${encodeURIComponent(referenceDecorationSvg('top'))}")`, '--document-footer-wave':`url("data:image/svg+xml,${encodeURIComponent(referenceDecorationSvg('footer'))}")` } : {}) } as CSSProperties;
}
export function blockDefaults(block: DocumentBlock, template?: DocumentTemplate): BlockStyle {
  const palette=documentPalette(template);
  return { fontSize: block.type === 'heading' ? block.level === 1 ? palette.font.title : block.level === 3 ? palette.font.subheading : palette.font.section : palette.font.body,
    bold: block.type === 'heading', spaceAfter: block.type === 'heading' && !template?.designTheme?.startsWith('purple-reference-') ? 5 : 3, ...block.style };
}
