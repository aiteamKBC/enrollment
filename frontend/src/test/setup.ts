import "@testing-library/jest-dom/vitest";
import { afterEach } from 'vitest';
import { clearCoachSessionCache } from '@/features/coach/shared/coachSessionCache';

afterEach(() => clearCoachSessionCache());

// Node's experimental global localStorage may shadow jsdom's implementation
// with `undefined` unless Node was started with a storage file. Restore the
// browser API for tests that exercise persisted UI preferences.
if (!window.localStorage) {
  const values = new Map<string, string>();
  const storage: Storage = {
    get length() { return values.size; },
    clear: () => values.clear(),
    getItem: key => values.get(key) ?? null,
    key: index => [...values.keys()][index] ?? null,
    removeItem: key => { values.delete(key); },
    setItem: (key, value) => { values.set(key, String(value)); },
  };
  Object.defineProperty(window, 'localStorage', { configurable: true, value: storage });
}

// dnd-kit's sensors query these under the hood; jsdom doesn't implement them.
if (!window.matchMedia) {
  window.matchMedia = (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  } as unknown as MediaQueryList);
}

if (!("ResizeObserver" in window)) {
  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  // @ts-expect-error -- test-only stub, not a full ResizeObserver implementation
  window.ResizeObserver = ResizeObserverStub;
}
