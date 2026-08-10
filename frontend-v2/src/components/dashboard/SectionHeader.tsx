/**
 * SectionHeader — Section title with optional count badge and "View All" link.
 * Extracted from Dashboard.tsx.
 * D-020: token-pure, no raw palette colors, no isDark ternaries.
 */

import { ArrowUpRight } from 'lucide-react';
import { Link } from 'react-router-dom';

interface SectionHeaderProps {
  icon: React.ElementType;
  title: string;
  count?: number;
  viewAllHref?: string;
  viewAllLabel?: string;
}

export function SectionHeader({
  icon: Icon,
  title,
  count,
  viewAllHref,
  viewAllLabel = 'View All',
}: SectionHeaderProps) {
  return (
    <div className="flex items-center justify-between mb-3">
      <div className="flex items-center gap-2">
        <Icon className="h-4 w-4 text-muted-foreground" />
        <h2 className="text-base font-semibold text-foreground">{title}</h2>
        {count !== undefined && (
          <span className="px-2 py-0.5 text-xs font-medium rounded-full bg-muted text-muted-foreground">
            {count}
          </span>
        )}
      </div>
      {viewAllHref && (
        <Link to={viewAllHref} className="text-sm text-primary hover:text-primary/80 font-medium flex items-center gap-1 group">
          {viewAllLabel}
          <ArrowUpRight className="h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5" />
        </Link>
      )}
    </div>
  );
}
