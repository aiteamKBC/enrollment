import { useState } from 'react';
import type { ReviewFormResponse } from '@/api/reviewForm';
import { btnSecondary } from '@/pages/users/components/ui';
import { DocumentPreview } from './DocumentPreview';
import { substitute } from './fields';
import type { IssuedTemplate } from './types';

export function ReviewSectionWording({ template, section }: { template: IssuedTemplate; section: string }) {
  const blocks = template.content.blocks;
  const end = blocks.findIndex((b) => b.source === `review:${section}`);
  let start = 0;
  for (let i = 0; i < end; i++) if (blocks[i].type === 'native') start = i + 1;
  const wording = blocks.slice(start, end < 0 ? undefined : end).filter((b) => ['paragraph', 'declaration', 'date', 'heading'].includes(b.type));
  return <div className="space-y-3 text-sm leading-relaxed">{wording.map((b) => b.type === 'heading'
    ? <h3 key={b.id} className="font-semibold">{substitute(b.text || '', template)}</h3>
    : <p key={b.id} className="whitespace-pre-wrap">{substitute(b.text || '', template)}</p>)}</div>;
}

export function ReviewTemplateWording({ data }: { data: ReviewFormResponse }) {
  const [preview, showPreview] = useState(false);
  if (!data.template || data.template.legacyEquivalent) return null;
  const template = data.template;
  return <section className="space-y-3 rounded-xl border border-primary-200 bg-background-50 p-4" aria-label="Issued review template">
    <p className="text-sm font-semibold">{template.content.title} · Template version {template.number}</p>
    <p className="text-xs text-foreground-600">Read this issued document’s wording before signing. Its template version stays with this review.</p>
    <button className={btnSecondary} onClick={() => showPreview(true)}>Preview issued review PDF</button>
    {preview && <DocumentPreview value={{ template, data: { ...data, template }, missingFields: [], sample: false,
      notice: 'Issued review preview — viewing does not change answers or signatures.', previewToken: null }} onClose={() => showPreview(false)} />}
  </section>;
}
