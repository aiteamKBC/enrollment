import { useState } from 'react';
import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { FullPageEditor } from '../FullPageEditor';
import { DocumentDesignControl } from '../DocumentDesignControl';
import { editorBrand } from '../brandedEditorTheme';
import { CURRENT_BRAND_ID } from '../brandedPurpleTheme';
import { documentPages, readPage } from '../pageDocument';
import { contentFor, keys, payloadFor } from './fixtures';
import type { DocumentTemplate } from '../types';

function Editor({ initial, presentationTheme }: { initial: DocumentTemplate; presentationTheme?: string }) {
  const [content, setContent] = useState(initial);
  return <><DocumentDesignControl content={content} presentationTheme={presentationTheme} onChange={setContent} />
    <FullPageEditor content={content} fields={[]} presentationTheme={presentationTheme} onChange={setContent} />
    <output data-testid="saved-content">{JSON.stringify(content)}</output></>;
}
describe('editable KBC/DfE document presentation', () => {
  it.each(keys)('uses the branded canvas for %s without adding branding to saved blocks', key => {
    const content = contentFor(key); content.designTheme = 'purple-reference-v2';
    const before = JSON.stringify(content);
    render(<Editor initial={content} />);
    expect(screen.getByRole('img', { name: 'Kent Business College' })).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'Department for Education' })).toBeInTheDocument();
    const surface = screen.getByRole('textbox', { name: 'Document page 1' });
    expect(surface.closest('.document-branded-editor')).not.toBeNull();
    expect(surface.getAttribute('contenteditable')).toBe('true');
    expect(surface.querySelector('img[alt="Kent Business College"]')).toBeNull();
    expect(surface.querySelector('[data-branded-section]')).not.toBeNull();
    expect(screen.getByLabelText(/Editor page 1 of/)).toBeInTheDocument();
    expect(screen.getByTestId('saved-content').textContent).toBe(before);
    expect(content).toEqual(JSON.parse(before));
  });
  it('renders persisted current presentations without altering a missing design ID', () => {
    const content = contentFor('apprenticeship-agreement'); delete content.designTheme;
    render(<Editor initial={content} presentationTheme={CURRENT_BRAND_ID} />);
    expect(screen.getByRole('combobox', { name: 'Document design' })).toHaveValue('purple-reference-v2');
    expect(screen.getByTestId('document-editor-paper')).toHaveClass('document-branded-editor');
    expect(JSON.parse(screen.getByTestId('saved-content').textContent!).designTheme).toBeUndefined();
  });
  it('reserves header space without writing effective 42 mm clearance into saved margins', () => {
    const content = contentFor('ilr'); content.designTheme = 'purple-reference-v2';
    render(<Editor initial={content} />);
    fireEvent.change(screen.getByLabelText('left margin (mm)'), { target: { value: '12' } });
    const saved = JSON.parse(screen.getByTestId('saved-content').textContent!);
    expect(saved.pageLayout.margins.left).toBe(12);
    expect(saved.pageLayout.margins.top).toBeLessThanOrEqual(35);
    expect(screen.getByTestId('document-editor-paper').style.paddingTop).toBe('42mm');
  });
  it('switches the existing design identifier immediately and retains legacy options', () => {
    const content = contentFor('ilr'); content.designTheme = 'classic';
    render(<Editor initial={content} />);
    const selector = screen.getByRole('combobox', { name: 'Document design' });
    expect(screen.queryByRole('img', { name: 'Kent Business College' })).toBeNull();
    fireEvent.change(selector, { target: { value: 'purple-reference-v2' } });
    expect(screen.getByRole('option', { name: 'KBC & DfE — Purple Branded Design' })).toBeInTheDocument();
    expect(screen.getByRole('img', { name: 'Kent Business College' })).toBeInTheDocument();
    fireEvent.change(selector, { target: { value: 'lavender-v1' } });
    expect(screen.queryByRole('img', { name: 'Kent Business College' })).toBeNull();
    expect(JSON.parse(screen.getByTestId('saved-content').textContent!).blocks).toEqual(content.blocks);
  });
  it('keeps identical current logo geometry while switching and reordering pages', () => {
    const content = contentFor('written-agreement'); content.designTheme = 'purple-reference-v2';
    render(<Editor initial={content} />);
    const style = screen.getByRole('img', { name: 'Kent Business College' }).getAttribute('style');
    const nav = screen.getByRole('navigation', { name: 'Document pages' });
    fireEvent.click(within(nav).getByRole('button', { name: /^Page 2/ }));
    expect(screen.getByRole('img', { name: 'Kent Business College' }).getAttribute('style')).toBe(style);
    expect(screen.getByRole('textbox', { name: 'Document page 2' })).toHaveAttribute('contenteditable', 'true');
    fireEvent.click(screen.getByRole('button', { name: 'Move page 2 up' }));
    expect(screen.getByRole('img', { name: 'Kent Business College' }).getAttribute('style')).toBe(style);
    expect(JSON.parse(screen.getByTestId('saved-content').textContent!).blocks[0].id).toBe(documentPages(content)[1].blocks[0].id);
  });
  it('leaves frozen legacy style settings and v3 presentations intact', () => {
    const content = contentFor('ilr'); content.designTheme = 'purple-reference-v2';
    expect(editorBrand(content, 'legacy')).toBeUndefined();
    render(<Editor initial={content} presentationTheme="legacy" />);
    expect(screen.queryByTestId('branded-page-furniture')).toBeNull();
    expect(screen.getByTestId('document-editor-paper')).toHaveClass('document-reference-v2');
    expect(editorBrand(content, 'kbc-dfe-purple-v3')).toBe('kbc-dfe-purple-v3');
  });
  it('edits actual text, keeps links/provenance and restores content with Undo/Redo', () => {
    const value = payloadFor('apprenticeship-agreement'); value.template.content.designTheme = 'purple-reference-v2';
    render(<Editor initial={value.template.content} />);
    const surface = screen.getByRole('textbox', { name: 'Document page 1' });
    const paragraph = surface.querySelector('p')!; paragraph.textContent = 'Editable revised wording'; fireEvent.input(surface);
    expect(screen.getByTestId('saved-content').textContent).toContain('Editable revised wording');
    const blocks = readPage(surface, documentPages(value.template.content)[0]);
    expect(JSON.stringify(blocks)).not.toContain('data:image/svg');
    expect(JSON.stringify(blocks)).not.toContain('data-branded-section');
    expect(blocks.filter(b => b.type === 'field')).toEqual(value.template.content.blocks.filter(b => b.type === 'field'));
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));
    expect(screen.getByTestId('saved-content').textContent).not.toContain('Editable revised wording');
    fireEvent.click(screen.getByRole('button', { name: 'Redo' }));
    expect(screen.getByTestId('saved-content').textContent).toContain('Editable revised wording');
  });
});
