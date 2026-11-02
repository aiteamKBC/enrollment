import type { ProgrammeModuleSelection } from './configuration';

export type ModuleSaveStatus = 'idle' | 'saving' | 'saved' | 'error';

/** One writer, one replaceable pending snapshot. Responses never own local edits. */
export class ModuleSaveQueue {
  private pending?: { selection: ProgrammeModuleSelection; editedAt: number };
  private running = false;
  private timer?: ReturnType<typeof setTimeout>;
  private save: (selection: ProgrammeModuleSelection) => Promise<void>;
  private onStatus: (status: ModuleSaveStatus, error?: unknown) => void;
  private listeners = new Set<(status: ModuleSaveStatus, error?: unknown) => void>();
  private currentStatus: ModuleSaveStatus = 'idle';
  private currentError?: unknown;
  private debounceMs: number;

  constructor(save: (selection: ProgrammeModuleSelection) => Promise<void>,
    status: (status: ModuleSaveStatus, error?: unknown) => void, debounceMs = 500) {
    this.save = save; this.onStatus = status; this.debounceMs = debounceMs;
  }

  subscribe(listener: (status: ModuleSaveStatus, error?: unknown) => void) {
    this.listeners.add(listener);
    listener(this.currentStatus, this.currentError);
    return () => { this.listeners.delete(listener); };
  }

  private status(status: ModuleSaveStatus, error?: unknown) {
    this.currentStatus = status; this.currentError = error;
    if (error === undefined) this.onStatus(status); else this.onStatus(status, error);
    this.listeners.forEach((listener) => listener(status, error));
  }

  enqueue(selection: ProgrammeModuleSelection) {
    this.pending = { selection, editedAt: Date.now() };
    this.status('saving');
    this.schedule();
  }

  retry() {
    if (!this.pending) return;
    this.status('saving');
    void this.flush();
  }

  flushOnLeave() {
    if (this.currentStatus === 'saving') void this.flush();
  }

  private schedule() {
    clearTimeout(this.timer);
    if (this.running || !this.pending) return;
    this.timer = setTimeout(() => { void this.flush(); }, Math.max(0, this.debounceMs - (Date.now() - this.pending.editedAt)));
  }

  async flush() {
    clearTimeout(this.timer);
    if (this.running || !this.pending) return;
    const job = this.pending;
    this.running = true;
    let succeeded = false;
    try {
      await this.save(job.selection);
      succeeded = true;
      if (job === this.pending) { this.pending = undefined; this.status('saved'); }
    } catch (error) {
      this.status('error', error);
    } finally {
      this.running = false;
      if (succeeded && this.pending) this.schedule();
    }
  }
}
