import { LucideIcon } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Sparkles } from 'lucide-react';
import { ReactNode } from 'react';

// ---------------------------------------------------------------------------
// Decorative SVG illustrations
// ---------------------------------------------------------------------------

/** Abstract dotted circle pattern — subtle background decoration */
function DottedRing({ className }: { className?: string }) {
  return (
    <svg
      className={cn('absolute text-muted-foreground/10', className)}
      width="120"
      height="120"
      viewBox="0 0 120 120"
      fill="none"
      aria-hidden="true"
    >
      <circle
        cx="60"
        cy="60"
        r="54"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeDasharray="4 6"
      />
      <circle
        cx="60"
        cy="60"
        r="38"
        stroke="currentColor"
        strokeWidth="1"
        strokeDasharray="3 8"
      />
    </svg>
  );
}

/** Small floating dots that drift around the icon for visual interest */
function FloatingDots() {
  return (
    <div className="absolute inset-0 pointer-events-none" aria-hidden="true">
      <div className="absolute top-2 left-3 w-1.5 h-1.5 rounded-full bg-primary/20 motion-safe:animate-float-slow" />
      <div className="absolute bottom-4 right-2 w-1 h-1 rounded-full bg-primary/15 motion-safe:animate-float-medium" />
      <div className="absolute top-1/2 -left-1 w-1 h-1 rounded-full bg-primary/10 motion-safe:animate-float-fast" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export interface EmptyStateProps {
  icon: LucideIcon;
  title: string;
  description: string;
  action?: {
    label: string;
    onClick: () => void;
    variant?: 'default' | 'outline' | 'secondary';
    icon?: ReactNode;
  };
  secondaryAction?: {
    label: string;
    onClick: () => void;
  };
  className?: string;
  /** Show a decorative sparkle accent on the icon (default: true) */
  showAccent?: boolean;
  /** Visual size variant (default: 'md') */
  size?: 'sm' | 'md' | 'lg';
  /** Show decorative background illustration (default: true) */
  illustration?: boolean;
}

export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  secondaryAction,
  className,
  showAccent = true,
  size = 'md',
  illustration = true,
}: EmptyStateProps) {
  const sizes = {
    sm: { wrapper: 'p-4', icon: 'p-3', iconSize: 'h-6 w-6', title: 'text-sm', desc: 'text-xs', gap: 'mb-3' },
    md: { wrapper: 'p-8', icon: 'p-6', iconSize: 'h-12 w-12', title: 'text-lg', desc: 'text-sm', gap: 'mb-4' },
    lg: { wrapper: 'p-12', icon: 'p-8', iconSize: 'h-16 w-16', title: 'text-xl', desc: 'text-base', gap: 'mb-6' },
  };

  const s = sizes[size];

  return (
    <div
      className={cn(
        'flex flex-1 items-center justify-center motion-safe:animate-fade-in',
        s.wrapper,
        className
      )}
      role="status"
      aria-label={title}
    >
      <div className="text-center max-w-md relative">
        {/* Background decorative ring */}
        {illustration && size !== 'sm' && (
          <DottedRing className="-top-6 left-1/2 -translate-x-1/2" />
        )}

        <div className={cn(s.gap, 'flex justify-center')}>
          <div className="relative group">
            {/* Outer glow ring */}
            <div className="absolute -inset-1 rounded-full bg-primary/10 opacity-0 group-hover:opacity-100 motion-safe:transition-opacity motion-safe:duration-300" />
            {/* Icon container */}
            <div
              className={cn(
                'relative rounded-full bg-muted motion-safe:transition-transform motion-safe:duration-200 group-hover:scale-105',
                s.icon
              )}
            >
              <Icon className={cn(s.iconSize, 'text-muted-foreground')} />
              {/* Floating dots */}
              {illustration && size !== 'sm' && <FloatingDots />}
            </div>
            {/* Sparkle accent */}
            {showAccent && size !== 'sm' && (
              <div className="absolute -top-1 -right-1 rounded-full bg-background p-1">
                <Sparkles className="h-4 w-4 text-primary/60" />
              </div>
            )}
          </div>
        </div>
        <h3 className={cn(s.title, 'font-semibold mb-2')}>{title}</h3>
        <p className={cn(s.desc, 'text-muted-foreground mb-6 leading-relaxed')}>{description}</p>
        {(action || secondaryAction) && (
          <div className="flex flex-col sm:flex-row gap-2 justify-center">
            {action && (
              <Button
                onClick={action.onClick}
                variant={action.variant || 'default'}
                size={size === 'sm' ? 'sm' : 'lg'}
              >
                {action.icon}
                {action.label}
              </Button>
            )}
            {secondaryAction && (
              <Button
                onClick={secondaryAction.onClick}
                variant="outline"
                size={size === 'sm' ? 'sm' : 'lg'}
              >
                {secondaryAction.label}
              </Button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
