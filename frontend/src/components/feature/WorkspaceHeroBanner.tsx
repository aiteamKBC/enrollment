/* This file intentionally exports the animation hook beside the banner so the
   existing page imports stay stable. */
/* eslint-disable react-refresh/only-export-components */
import { useEffect, useRef, type ReactNode } from 'react';
import { AppIcon } from '@/components/feature/AppIcon';
import design from './WorkspaceDesign.module.css';

interface WorkspaceHeroBannerProps {
  title: string;
  description: ReactNode;
  icon: string;
  imageUrl?: string;
  imageAlt?: string;
  stats?: { label: string; value: ReactNode; icon?: string; variant?: 'default' | 'danger' | 'success' | 'warning' }[];
  statIconPosition?: 'inline' | 'leading';
  accentColor?: string;
  actions?: ReactNode;
  eyebrow?: string;
  className?: string;
  decorative?: boolean;
  footer?: ReactNode;
  visual?: ReactNode;
  heading?: 'h1' | 'h2';
}

export function WorkspaceHeroBanner({
  title,
  description,
  stats,
  icon,
  statIconPosition = 'inline',
  actions,
  eyebrow,
  className = '',
  decorative = false,
  footer,
  visual,
  heading: Heading = 'h2',
}: WorkspaceHeroBannerProps) {
  return (
    <div
      className={`ui-hero-banner workspace-hero-banner relative overflow-hidden rounded-2xl border border-primary-200/60 bg-primary-50/60 ${design.hero} ${className}`}
    >
      {decorative && (
        <>
          <span aria-hidden="true" className="workspace-hero-decoration workspace-hero-decoration--ring" />
          <span aria-hidden="true" className="workspace-hero-decoration workspace-hero-decoration--dots" />
        </>
      )}

      <div className="workspace-hero-banner__content relative z-10 flex flex-wrap items-center gap-5 p-5 md:p-6">
        <div className="workspace-hero-banner__identity flex min-w-0 flex-[1_1_20rem] items-center gap-4">
          {icon && <span className="workspace-hero-banner__icon flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-primary-100/60 text-primary-600" aria-hidden="true">
            <AppIcon className={`${icon} h-6 w-6`}></AppIcon>
          </span>}

          <div className="min-w-0 flex-1">
            {eyebrow && <p className="workspace-hero-eyebrow mb-2 text-[10px] font-semibold uppercase tracking-widest text-primary-600">{eyebrow}</p>}
            <Heading className="workspace-hero-title font-heading text-xl font-semibold tracking-tight text-primary-800 md:text-2xl">{title}</Heading>
            <p className="workspace-hero-description mt-2 text-[13px] leading-relaxed text-foreground-500">{description}</p>
            {footer}
          </div>
        </div>

        {visual}
        {stats && stats.length > 0 && (
          <div className="workspace-hero-banner__stats flex max-w-full flex-wrap items-center gap-3">
            {stats.map((stat, i) => (
              <div key={i} className={`coach-metric-card workspace-hero-metric workspace-hero-metric--${stat.variant || 'default'} min-w-[80px] ${statIconPosition === 'leading' ? 'workspace-hero-metric--leading-icon' : ''}`}>
                {statIconPosition === 'leading' ? (
                  <>
                    {stat.icon && (
                      <span aria-hidden="true" className="workspace-hero-metric__icon flex shrink-0 items-center justify-center rounded-full">
                        <AppIcon className="text-current" name={stat.icon} size={20} />
                      </span>
                    )}
                    <span className="workspace-hero-metric__body flex min-w-0 flex-col">
                      <span className="workspace-hero-metric__value tabular-nums">{stat.value}</span>
                      <span className="workspace-hero-metric__label truncate">{stat.label}</span>
                    </span>
                  </>
                ) : (
                  <>
                    <p className="workspace-hero-metric__status flex items-center justify-between gap-1.5 whitespace-nowrap text-[10px] font-medium uppercase tracking-wide text-foreground-500">
                      <span>{stat.label}</span>
                      {stat.icon && <span className="workspace-hero-metric__status-icon flex shrink-0 items-center justify-center rounded-full"><AppIcon className="text-current" name={stat.icon} size={14} /></span>}
                    </p>
                    <p className="mt-1 text-[28px] font-semibold leading-none tabular-nums text-foreground-900">{stat.value}</p>
                  </>
                )}
              </div>
            ))}
          </div>
        )}

        {actions ? <div className="workspace-hero-banner__actions flex max-w-full flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
    </div>
  );
}

/* Animation helper for staggered children */
export function useStaggerAnimation(delay = 0) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const children = el.children;
    for (let i = 0; i < children.length; i++) {
      const child = children[i] as HTMLElement;
      child.style.opacity = '0';
      child.style.transform = 'translateY(8px)';
      setTimeout(() => {
        child.style.transition = 'opacity 0.4s ease-out, transform 0.4s ease-out';
        child.style.opacity = '1';
        child.style.transform = 'translateY(0)';
      }, delay + i * 80);
    }
  }, [delay]);

  return ref;
}
