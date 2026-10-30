import { Link } from 'react-router-dom';
import { ArrowRight, type LucideIcon } from 'lucide-react';
import { cn } from '@/lib/cn';
import styles from './seasonal-hover-cards.module.css';

export interface SeasonCardProps {
  title: string;
  subtitle: string;
  description: string;
  imageSrc?: string;
  imageAlt?: string;
  href?: string;
  onClick?: () => void;
  cta?: string;
  status?: string;
  meta?: string;
  icon?: LucideIcon;
  variant?: 'action' | 'session' | 'due';
  className?: string;
}

interface SeasonalHoverCardsProps {
  cards: SeasonCardProps[];
  className?: string;
  theme?: 'default' | 'hero';
  layout?: 'row' | 'inline';
}

function SeasonCard({
  title, subtitle, description, href, onClick, cta, status, icon: Icon,
  variant = 'action', className,
}: SeasonCardProps) {
  const classes = cn(styles.card, styles[variant], className);
  const external = !!href && /^https?:\/\//i.test(href);
  const hasAction = Boolean(cta && (href || onClick));
  const content = <>
    <div className={styles.cardPrimary}>
      <div className={styles.topLine}>
        <span className={styles.icon} aria-hidden="true">
          {Icon ? <Icon /> : <span className={styles.iconFallback} />}
        </span>
        <span className={styles.headingCopy}>
          <span className={styles.eyebrow}>{title}</span>
          <strong className={styles.subtitle}>{subtitle}</strong>
        </span>
      </div>
      <p className={styles.description}>{description}</p>
      {hasAction && href && (external
        ? <a href={href} className={styles.actionButton} target="_blank" rel="noopener noreferrer">{cta}<ArrowRight aria-hidden="true" /></a>
        : <Link to={href} className={styles.actionButton}>{cta}<ArrowRight aria-hidden="true" /></Link>)}
      {hasAction && !href && onClick && <button type="button" className={styles.actionButton} onClick={onClick}>{cta}<ArrowRight aria-hidden="true" /></button>}
      {!hasAction && status && <span className={styles.status}><span aria-hidden="true" />{status}</span>}
    </div>
  </>;

  return <div className={classes} role="group" aria-label={`${title}: ${subtitle}`}>{content}</div>;
}

export function SeasonalHoverCards({ cards, className, theme = 'default', layout = 'row' }: SeasonalHoverCardsProps) {
  return <div role="region" className={cn(styles.cards, theme === 'hero' && styles.hero, layout === 'inline' && styles.inline, className)} aria-label="Learner actions">
    {cards.map((card, index) => <SeasonCard key={`${card.title}-${index}`} {...card} />)}
  </div>;
}
