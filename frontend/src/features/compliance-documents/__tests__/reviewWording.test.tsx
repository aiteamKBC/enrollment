import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ReviewSectionWording, ReviewTemplateWording } from '../ReviewTemplateWording';
import { payloadFor, versionFor } from './fixtures';
import type { ReviewFormResponse } from '@/api/reviewForm';

describe('issued review wording', () => {
  it('shows edited issued ULN wording as safe text before signing', () => {
    const template = versionFor('review-uln', true);
    const intro = template.content.blocks.find((b) => b.type === 'paragraph')!;
    intro.text = 'Approved notice for {{learner.fullName}} <script>literal</script>';
    const { container } = render(<ReviewSectionWording template={template} section="ulnPrivacyNotice" />);
    expect(screen.getByText('Approved notice for SYNTHETIC Learner <script>literal</script>')).toBeInTheDocument();
    expect(container.querySelector('script')).toBeNull();
  });
  it('exposes the pinned issued PDF for amended reviews without changing answers', () => {
    const payload = payloadFor('review-uln', true); payload.data.answers = { ulnPrivacyNotice: { noticeRead: 'Yes' } };
    const before = structuredClone(payload.data.answers);
    render(<ReviewTemplateWording data={payload.data as ReviewFormResponse} />);
    expect(screen.getByRole('button', { name: 'Preview issued review PDF' })).toBeInTheDocument();
    expect(payload.data.answers).toEqual(before);
  });
  it('leaves the original review layout unchanged for legacy templates', () => {
    const { container } = render(<ReviewTemplateWording data={payloadFor('review-uln').data as ReviewFormResponse} />);
    expect(container).toBeEmptyDOMElement();
  });
});
