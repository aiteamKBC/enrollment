import { useState } from 'react';
import { beforeEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { FullPageEditor } from '../FullPageEditor';
import { contentFor } from './fixtures';
import type { DocumentTemplate, FieldDefinition } from '../types';
const fields: FieldDefinition[] = [
  { key: 'learner.fullName', label: 'Learner name', group: 'Learner', format: 'text' },
  { key: 'programme.startDate', label: 'Start date', group: 'Programme', format: 'date' },
];
function initial(): DocumentTemplate {
  return { ...contentFor('review-uln'), designTheme: 'classic', signatureRoles: ['learner'], blocks: [
    { id: 'text', type: 'paragraph', text: 'Select this text' },
    { id: 'table', type: 'table', columns: ['First', 'Second'], rows: [['One', 'Two'], ['Three', 'Four']] },
  ] };
}
function Harness({ availableFields = fields }: { availableFields?: FieldDefinition[] }) {
  const [content, update] = useState(initial());
  return <><FullPageEditor content={content} fields={availableFields} onChange={update} /><output aria-label="Saved content">{JSON.stringify(content)}</output></>;
}
const exec = vi.fn();
beforeEach(() => {
  vi.clearAllMocks();
  Object.defineProperty(document, 'execCommand', { configurable: true, value: exec });
  Object.defineProperty(document, 'queryCommandState', { configurable: true, value: vi.fn(() => false) });
  Object.defineProperty(document, 'queryCommandIndeterm', { configurable: true, value: vi.fn(() => false) });
});
function selectText(selector = 'p') {
  const editor = screen.getByRole('textbox', { name: 'Document page 1' });
  const target = editor.querySelector(selector)!;
  const range = document.createRange(); range.selectNodeContents(target);
  window.getSelection()!.removeAllRanges(); window.getSelection()!.addRange(range);
  fireEvent.mouseUp(editor);
  return target;
}

it('groups familiar labelled icons and retains dropdowns and unavailable states', () => {
  render(<Harness availableFields={[]} />);
  const toolbar = within(screen.getByRole('toolbar', { name: 'Document formatting' }));
  for (const name of ['Text formatting', 'Font and paragraph', 'Alignment', 'Lists', 'Tables', 'Document fields and insert', 'History']) expect(toolbar.getByRole('group', { name })).toBeVisible();
  for (const name of ['Bold', 'Italic', 'Underline', 'Align left', 'Align centre', 'Align right', 'Bullet list', 'Numbered list', 'Undo', 'Redo', 'Insert date', 'Insert page break']) {
    const button = toolbar.getByRole('button', { name });
    expect(button).toHaveAttribute('title', name); expect(button.querySelector('svg')).not.toBeNull();
  }
  expect(toolbar.getByRole('combobox', { name: 'Font size' })).toBeVisible();
  expect(toolbar.getByRole('combobox', { name: 'Paragraph style' })).toBeVisible();
  expect(toolbar.getByRole('combobox', { name: 'Learner or document field' })).toBeVisible();
  for (const name of ['Insert dynamic field', 'Insert date', 'Undo', 'Redo']) expect(toolbar.getByRole('button', { name })).toBeDisabled();
  expect(toolbar.getByText('Dynamic field')).toBeVisible(); expect(toolbar.getByText('Signature block')).toBeVisible();
});

it.each([
  ['Bold', 'bold'], ['Italic', 'italic'], ['Underline', 'underline'], ['Align left', 'justifyLeft'],
  ['Align centre', 'justifyCenter'], ['Align right', 'justifyRight'], ['Bullet list', 'insertUnorderedList'], ['Numbered list', 'insertOrderedList'],
])('keeps the existing %s command and restores the saved text selection', async (label, command) => {
  render(<Harness />); selectText();
  const selected = window.getSelection()!.toString();
  await userEvent.click(screen.getByRole('button', { name: label }));
  expect(exec).toHaveBeenCalledWith(command, false, undefined);
  expect(window.getSelection()!.toString()).toBe(selected);
});

it('only shows a selected state when the editor reports that formatting', () => {
  render(<Harness />);
  vi.mocked(document.queryCommandState).mockImplementation((command) => command === 'bold');
  selectText();
  expect(screen.getByRole('button', { name: 'Bold' })).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByRole('button', { name: 'Italic' })).toHaveAttribute('aria-pressed', 'false');
  vi.mocked(document.queryCommandIndeterm).mockReturnValue(true);
  selectText();
  expect(screen.getByRole('button', { name: 'Bold' })).not.toHaveAttribute('aria-pressed');
});

it.each(['Add row', 'Remove row', 'Add column', 'Remove column'])('preserves the selected table cell through the menu for %s and supports undo/redo', async (action) => {
  render(<Harness />); selectText('tbody td');
  const editor = screen.getByRole('textbox', { name: 'Document page 1' });
  const before = editor.querySelector('table')!.outerHTML;
  const original = screen.getByLabelText('Saved content').textContent;
  await userEvent.click(screen.getByRole('button', { name: 'Table' }));
  expect(screen.getAllByRole('menuitem')).toHaveLength(5);
  await userEvent.click(screen.getByRole('menuitem', { name: action }));
  expect(screen.queryByRole('menu')).toBeNull();
  expect(editor.querySelector('table')!.outerHTML).not.toBe(before);
  expect(screen.getByLabelText('Saved content').textContent).not.toBe(original);
  await userEvent.click(screen.getByRole('button', { name: 'Undo' }));
  expect(screen.getByLabelText('Saved content')).toHaveTextContent(original!);
  await userEvent.click(screen.getByRole('button', { name: 'Redo' }));
  expect(screen.getByLabelText('Saved content').textContent).not.toBe(original);
});

it('opens and navigates the Table menu by keyboard and dismisses it with Escape or outside click', async () => {
  render(<Harness />); selectText('tbody td');
  const button = screen.getByRole('button', { name: 'Table' }); button.focus();
  await userEvent.keyboard('{ArrowDown}');
  await waitFor(() => expect(screen.getByRole('menuitem', { name: 'Add table' })).toHaveFocus());
  await userEvent.keyboard('{End}'); expect(screen.getByRole('menuitem', { name: 'Remove column' })).toHaveFocus();
  await userEvent.keyboard('{Home}{ArrowDown}{Enter}');
  expect(screen.queryByRole('menu')).toBeNull();
  await userEvent.click(button); await userEvent.keyboard('{Escape}');
  expect(button).toHaveFocus(); expect(screen.queryByRole('menu')).toBeNull();
  await userEvent.click(button); fireEvent.mouseDown(document.body); expect(screen.queryByRole('menu')).toBeNull();
});

it('retains table insertion, dropdown commands, dynamic field bindings and date insertion', async () => {
  render(<Harness />); selectText();
  await userEvent.selectOptions(screen.getByLabelText('Font size'), '5'); expect(exec).toHaveBeenLastCalledWith('fontSize', false, '5');
  await userEvent.selectOptions(screen.getByLabelText('Paragraph style'), 'h2'); expect(exec).toHaveBeenLastCalledWith('formatBlock', false, 'h2');
  await userEvent.selectOptions(screen.getByLabelText('Learner or document field'), 'learner.fullName');
  await userEvent.click(screen.getByRole('button', { name: 'Insert dynamic field' }));
  expect(exec).toHaveBeenLastCalledWith('insertHTML', false, expect.stringContaining('data-field-key="learner.fullName"'));
  await userEvent.click(screen.getByRole('button', { name: 'Insert date' }));
  expect(exec).toHaveBeenLastCalledWith('insertHTML', false, expect.stringContaining('data-field-key="programme.startDate"'));
  await userEvent.click(screen.getByRole('button', { name: 'Table' }));
  await userEvent.click(screen.getByRole('menuitem', { name: 'Add table' }));
  expect(exec).toHaveBeenLastCalledWith('insertHTML', false, expect.stringContaining('<table'));
});

it('retains signature insertion and page breaks in the saved document', async () => {
  render(<Harness />);
  await userEvent.click(screen.getByRole('button', { name: 'Insert signature block' }));
  let saved = JSON.parse(screen.getByLabelText('Saved content').textContent!);
  expect(saved.blocks).toContainEqual(expect.objectContaining({ type: 'signature', role: 'learner' }));
  await userEvent.click(screen.getByRole('button', { name: 'Insert page break' }));
  saved = JSON.parse(screen.getByLabelText('Saved content').textContent!);
  expect(saved.blocks).toContainEqual(expect.objectContaining({ type: 'page-break' }));
});
