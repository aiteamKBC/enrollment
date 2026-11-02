import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ModuleSaveQueue } from '../modules/moduleSaveQueue';
import type { ProgrammeModuleSelection } from '../modules/configuration';

const choice = (id: string): ProgrammeModuleSelection => ({ programme: 'PCP_L6', pathway: 'operational',
  configurationVersion: 11, modulesByPathway: { operational: [id] } });
beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

it('debounces rapid edits for 500ms into one authoritative save', async () => {
  const save = vi.fn(async (_value: ProgrammeModuleSelection) => {}); const status = vi.fn(); const queue = new ModuleSaveQueue(save, status);
  queue.enqueue(choice('a'));
  await vi.advanceTimersByTimeAsync(300);
  queue.enqueue(choice('b'));
  await vi.advanceTimersByTimeAsync(499);
  expect(save).not.toHaveBeenCalled();
  await vi.advanceTimersByTimeAsync(1);
  expect(save).toHaveBeenCalledExactlyOnceWith(choice('b'));
  expect(status).toHaveBeenLastCalledWith('saved');
});

it('never overlaps requests and coalesces all changes made during an in-flight save', async () => {
  let release!: () => void;
  const save = vi.fn(async (_value: ProgrammeModuleSelection) => {}).mockImplementationOnce(() => new Promise<void>((resolve) => { release = resolve; }));
  const status = vi.fn(); const queue = new ModuleSaveQueue(save, status);
  queue.enqueue(choice('a')); await vi.advanceTimersByTimeAsync(500);
  queue.enqueue(choice('b')); queue.enqueue(choice('c'));
  await vi.advanceTimersByTimeAsync(800);
  expect(save).toHaveBeenCalledTimes(1);
  release(); await vi.advanceTimersByTimeAsync(0);
  expect(save.mock.calls.map(([value]) => value)).toEqual([choice('a'), choice('c')]);
  expect(status.mock.calls.filter(([value]) => value === 'saved')).toHaveLength(1);
});

it('does not retry errors indefinitely and retries the newest unsaved snapshot on demand', async () => {
  const save = vi.fn(async (_value: ProgrammeModuleSelection) => {}).mockRejectedValueOnce(new Error('Unavailable'));
  const status = vi.fn(); const queue = new ModuleSaveQueue(save, status);
  queue.enqueue(choice('a')); await vi.advanceTimersByTimeAsync(500);
  expect(status).toHaveBeenLastCalledWith('error', expect.any(Error));
  await vi.advanceTimersByTimeAsync(10000);
  expect(save).toHaveBeenCalledTimes(1);
  queue.retry(); await vi.advanceTimersByTimeAsync(0);
  expect(save).toHaveBeenCalledTimes(2);
  expect(status).toHaveBeenLastCalledWith('saved');
});

it('flushes pending changes when leaving the module step without a duplicate timer request', async () => {
  const save = vi.fn(async (_value: ProgrammeModuleSelection) => {});
  const queue = new ModuleSaveQueue(save, vi.fn());
  queue.enqueue(choice('a')); await queue.flush(); await vi.advanceTimersByTimeAsync(1000);
  expect(save).toHaveBeenCalledExactlyOnceWith(choice('a'));
});

it('keeps one writer across step subscriptions and never retries an error just because a step unmounts', async () => {
  const save = vi.fn(async (_value: ProgrammeModuleSelection) => {}).mockRejectedValueOnce(new Error('Unavailable'));
  const queue = new ModuleSaveQueue(save, vi.fn());
  const first = vi.fn(); const unsubscribe = queue.subscribe(first);
  queue.enqueue(choice('a')); await vi.advanceTimersByTimeAsync(500);
  unsubscribe(); queue.flushOnLeave();
  const second = vi.fn(); queue.subscribe(second);
  expect(second).toHaveBeenLastCalledWith('error', expect.any(Error));
  expect(save).toHaveBeenCalledTimes(1);
  queue.retry(); await vi.advanceTimersByTimeAsync(0);
  expect(second).toHaveBeenLastCalledWith('saved', undefined);
  expect(save).toHaveBeenCalledTimes(2);
});
