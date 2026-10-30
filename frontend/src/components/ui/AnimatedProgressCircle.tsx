import type { CSSProperties, SVGProps } from 'react';
import styles from './AnimatedProgressCircle.module.css';

/** Draws a progress arc when its SVG circle appears or its value changes. */
export function AnimatedProgressCircle({ className, style, strokeDasharray, strokeDashoffset, ...props }: SVGProps<SVGCircleElement>) {
  const circumference = Number(strokeDasharray);
  const animateOffset = strokeDashoffset != null && Number.isFinite(circumference);

  return <circle
    key={`${strokeDasharray}:${strokeDashoffset ?? ''}`}
    {...props}
    strokeDasharray={strokeDasharray}
    strokeDashoffset={strokeDashoffset}
    className={`${className ?? ''} ${animateOffset ? styles.offset : styles.dash}`}
    style={{ ...style, '--ring-start-offset': circumference } as CSSProperties}
  />;
}
