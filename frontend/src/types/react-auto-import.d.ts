import type * as ReactTypes from 'react';

// unplugin-auto-import supplies the React value at runtime and declares it in
// auto-imports.d.ts. TypeScript 5/React 19 no longer exposes React's type
// namespace globally to module files, so legacy `React.MouseEvent` annotations
// need a matching type-only namespace. Keeping the aliases here avoids adding a
// runtime React import to every auto-imported component.
declare global {
  const AppIcon: typeof import('@/components/feature/AppIcon')['AppIcon'];
  const Link: typeof import('react-router-dom')['Link'];
  const NavLink: typeof import('react-router-dom')['NavLink'];
  const Navigate: typeof import('react-router-dom')['Navigate'];
  const Outlet: typeof import('react-router-dom')['Outlet'];
  const React: typeof import('react')['default'];
  const Trans: typeof import('react-i18next')['Trans'];
  const cloneElement: typeof import('react')['cloneElement'];
  const createContext: typeof import('react')['createContext'];
  const createElement: typeof import('react')['createElement'];
  const forwardRef: typeof import('react')['forwardRef'];
  const isValidElement: typeof import('react')['isValidElement'];
  const lazy: typeof import('react')['lazy'];
  const memo: typeof import('react')['memo'];
  const startTransition: typeof import('react')['startTransition'];
  const useCallback: typeof import('react')['useCallback'];
  const useContext: typeof import('react')['useContext'];
  const useDebugValue: typeof import('react')['useDebugValue'];
  const useDeferredValue: typeof import('react')['useDeferredValue'];
  const useEffect: typeof import('react')['useEffect'];
  const useId: typeof import('react')['useId'];
  const useImperativeHandle: typeof import('react')['useImperativeHandle'];
  const useInsertionEffect: typeof import('react')['useInsertionEffect'];
  const useLayoutEffect: typeof import('react')['useLayoutEffect'];
  const useLocation: typeof import('react-router-dom')['useLocation'];
  const useMemo: typeof import('react')['useMemo'];
  const useNavigate: typeof import('react-router-dom')['useNavigate'];
  const useParams: typeof import('react-router-dom')['useParams'];
  const useReducer: typeof import('react')['useReducer'];
  const useRef: typeof import('react')['useRef'];
  const useSearchParams: typeof import('react-router-dom')['useSearchParams'];
  const useState: typeof import('react')['useState'];
  const useSyncExternalStore: typeof import('react')['useSyncExternalStore'];
  const useTransition: typeof import('react')['useTransition'];
  const useTranslation: typeof import('react-i18next')['useTranslation'];

  namespace React {
    type ButtonHTMLAttributes<T> = ReactTypes.ButtonHTMLAttributes<T>;
    type ChangeEvent<T = Element> = ReactTypes.ChangeEvent<T>;
    type ComponentPropsWithoutRef<T extends ReactTypes.ElementType> = ReactTypes.ComponentPropsWithoutRef<T>;
    type CSSProperties = ReactTypes.CSSProperties;
    type DragEvent<T = Element> = ReactTypes.DragEvent<T>;
    type ElementRef<T extends ReactTypes.ElementType> = ReactTypes.ComponentRef<T>;
    type FormEvent<T = Element> = ReactTypes.FormEvent<T>;
    type HTMLAttributes<T> = ReactTypes.HTMLAttributes<T>;
    type KeyboardEvent<T = Element> = ReactTypes.KeyboardEvent<T>;
    type MouseEvent<T = Element, E = globalThis.MouseEvent> = ReactTypes.MouseEvent<T, E>;
    type PointerEvent<T = Element> = ReactTypes.PointerEvent<T>;
    type ReactNode = ReactTypes.ReactNode;
    type TdHTMLAttributes<T> = ReactTypes.TdHTMLAttributes<T>;
    type ThHTMLAttributes<T> = ReactTypes.ThHTMLAttributes<T>;
    type TouchEvent<T = Element> = ReactTypes.TouchEvent<T>;
  }

  namespace JSX {
    type Element = ReactTypes.JSX.Element;
  }
}

export {};
