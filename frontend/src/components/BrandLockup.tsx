type BrandLockupProps = {
  size?: 'hero' | 'default' | 'compact';
  theme?: 'dark' | 'light';
  className?: string;
};

const SIZE_CLASSES = {
  hero: 'h-20 w-48',
  default: 'h-16 w-36',
  compact: 'h-11 w-24',
} as const;

export function BrandLockup({ size = 'default', theme = 'light', className = '' }: BrandLockupProps) {
  void theme;
  return (
    <img
      src="/kbc-logo.png"
      alt="Kent Business College"
      className={`${SIZE_CLASSES[size]} shrink-0 object-contain object-left drop-shadow-[0_5px_12px_rgba(0,0,0,0.2)] ${className}`}
    />
  );
}
