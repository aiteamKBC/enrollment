import { useState, type ReactNode } from 'react';
import { readingSurfaceStyle, type ReadingPreferences } from './readingAccessibility';
import styles from './ReadingSurface.module.css';

export function ReadingSurface({ preferences, children, label, className = '' }: {
  preferences: ReadingPreferences; children: ReactNode; label: string; className?: string;
}) {
  const [rulerY, setRulerY] = useState(40);
  return <div role="region" aria-label={label} tabIndex={0}
    className={`${styles.surface} ${className}`} style={readingSurfaceStyle(preferences)}
    onMouseMove={event => {
      if (preferences.ruler) setRulerY(event.clientY - event.currentTarget.getBoundingClientRect().top + event.currentTarget.scrollTop);
    }}
    onPointerMove={event => {
      if (preferences.ruler) setRulerY(event.clientY - event.currentTarget.getBoundingClientRect().top + event.currentTarget.scrollTop);
    }}
    onKeyDown={event => {
      if (!preferences.ruler || event.target !== event.currentTarget || !['ArrowUp', 'ArrowDown'].includes(event.key)) return;
      event.preventDefault();
      const bottom = Math.max(40, event.currentTarget.scrollHeight - 18);
      setRulerY(value => Math.min(bottom, Math.max(18, value + (event.key === 'ArrowDown' ? 24 : -24))));
    }}>
    {preferences.ruler && <span aria-hidden="true" data-reading-ruler
      className="pointer-events-none absolute inset-x-0 z-20 h-9 border-y border-blue-400/50 bg-blue-300/20"
      style={{ top: Math.max(0, rulerY - 18) }} />}
    {children}
  </div>;
}
