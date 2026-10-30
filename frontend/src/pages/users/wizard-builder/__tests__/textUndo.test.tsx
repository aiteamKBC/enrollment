/**
 * Undo and redo in the builder's wording boxes: the history rules, and the
 * Undo/Redo buttons and Ctrl+Z / Ctrl+Y on a real editor.
 */
import { useState } from 'react';
import { describe, expect, it, beforeAll } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { EMPTY_HISTORY, TYPING_PAUSE_MS, record, redo, undo } from '../textHistory';
import { TextEditor } from '../TextEditor';
import { TEXT_SLOTS } from '../../wizard/layout/texts';

beforeAll(() => {
  (globalThis as Record<string, unknown>).AppIcon = ({ className }: { className?: string }) => <i className={className} />;
});

describe('the history rules', () => {
  it('groups a burst of typing into one step, but not typing after a pause', () => {
    let h = record(EMPTY_HISTORY, 'a', 'type', 1000);
    h = record(h, 'ab', 'type', 1200);
    expect(h.past).toEqual(['a']);
    h = record(h, 'abc', 'type', 1200 + TYPING_PAUSE_MS + 1);
    expect(h.past).toEqual(['a', 'abc']);
  });

  it('makes every toolbar action its own step', () => {
    let h = record(EMPTY_HISTORY, 'a', 'type', 1000);
    h = record(h, 'ab', 'action', 1100);
    h = record(h, '**ab**', 'type', 1200);
    expect(h.past).toEqual(['a', 'ab', '**ab**']);
  });

  it('undoes, redoes, and forgets the redo once something new is typed', () => {
    const h = record(EMPTY_HISTORY, 'one', 'action', 0);
    const back = undo(h, 'two')!;
    expect(back.value).toBe('one');
    const forward = redo(back.history, 'one')!;
    expect(forward.value).toBe('two');
    expect(record(back.history, 'one', 'type', 5).future).toEqual([]);
    expect(undo(EMPTY_HISTORY, 'x')).toBeNull();
    expect(redo(EMPTY_HISTORY, 'x')).toBeNull();
  });
});

/** The editor wired to state, as the builder wires it. */
function Harness({ slotKey }: { slotKey: string }) {
  const [texts, setTexts] = useState<Record<string, string> | undefined>(undefined);
  return (
    <TextEditor
      slot={TEXT_SLOTS[slotKey]}
      texts={texts}
      onChange={(v) => setTexts((t) => {
        const next = { ...(t ?? {}) };
        if (v === undefined) delete next[slotKey];
        else next[slotKey] = v;
        return next;
      })}
    />
  );
}

describe('undo in the wording editor', () => {
  it('Ctrl+Z takes back typing and Ctrl+Y puts it back', async () => {
    render(<Harness slotKey="block.introduction.title" />);
    const box = screen.getByLabelText('Heading');
    const undoButton = screen.getByRole('button', { name: 'Undo change to Heading' });
    expect(undoButton).toBeDisabled();

    await userEvent.type(box, ' to KBC');
    expect(box).toHaveValue('Welcome to KBC');
    expect(undoButton).toBeEnabled();

    await userEvent.keyboard('{Control>}z{/Control}');
    expect(box).toHaveValue('Welcome');
    expect(screen.queryByText('Edited')).not.toBeInTheDocument();

    await userEvent.keyboard('{Control>}y{/Control}');
    expect(box).toHaveValue('Welcome to KBC');
    await userEvent.keyboard('{Control>}{Shift>}z{/Shift}{/Control}');
    expect(box).toHaveValue('Welcome to KBC');
  });

  it('the Undo button reverses toolbar formatting and a reset to the standard wording', async () => {
    render(<Harness slotKey="block.introduction.body" />);
    const body = screen.getByLabelText('Page text') as HTMLTextAreaElement;
    await userEvent.clear(body);
    await userEvent.type(body, 'Hello');
    body.setSelectionRange(0, 5);
    await userEvent.click(screen.getByRole('button', { name: 'Bold' }));
    expect(body.value).toBe('**Hello**');

    const undoButton = screen.getByRole('button', { name: 'Undo change to Page text' });
    await userEvent.click(undoButton);
    expect(body.value).toBe('Hello');

    await userEvent.click(screen.getByRole('button', { name: 'Reset to standard wording' }));
    expect(body.value.startsWith('## Shaping Tomorrow')).toBe(true);
    await userEvent.click(undoButton);
    expect(body.value).toBe('Hello');

    await userEvent.click(screen.getByRole('button', { name: 'Redo change to Page text' }));
    expect(body.value.startsWith('## Shaping Tomorrow')).toBe(true);
  });
});
