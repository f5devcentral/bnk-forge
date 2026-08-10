/**
 * Collapsible Section Component
 * PERF-016: Added onOpenChange callback for lazy loading
 */

import React, { useState } from 'react';
import { useThemeClasses } from '@/context/ThemeContext';
import { cn } from '@/lib/utils';
import { ChevronDown } from 'lucide-react';

export function CollapsibleSection({
  title,
  icon: Icon,
  children,
  defaultOpen = false,
  onOpenChange,
}: {
  title: string;
  icon: React.ElementType;
  children: React.ReactNode;
  defaultOpen?: boolean;
  onOpenChange?: (isOpen: boolean) => void;
}) {
  const { borderDefault, bgCard, bgCardHover, textSecondary, textPrimary } = useThemeClasses();
  const [isOpen, setIsOpen] = useState(defaultOpen);

  const handleToggle = () => {
    const newState = !isOpen;
    setIsOpen(newState);
    onOpenChange?.(newState);
  };

  return (
    <div className={cn('border rounded-xl overflow-hidden', borderDefault)}>
      <button
        onClick={handleToggle}
        aria-expanded={isOpen}
        className={cn(
          'w-full flex items-center justify-between p-4 transition-colors',
          bgCard, bgCardHover
        )}
      >
        <div className="flex items-center gap-3">
          <Icon className={cn('h-5 w-5', textSecondary)} />
          <span className={cn('font-medium', textPrimary)}>{title}</span>
        </div>
        <ChevronDown className={cn(
          'h-5 w-5 transition-transform',
          textSecondary,
          isOpen && 'rotate-180'
        )} />
      </button>
      {isOpen && (
        <div className={cn('p-4 border-t', borderDefault)}>
          {children}
        </div>
      )}
    </div>
  );
}
