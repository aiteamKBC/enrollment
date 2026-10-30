import "@testing-library/jest-dom/vitest";
import { afterEach } from 'vitest';
import { clearCoachSessionCache } from '@/features/coach/shared/coachSessionCache';

afterEach(() => clearCoachSessionCache());

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
