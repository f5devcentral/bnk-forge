import { Package, CheckCircle2, XCircle } from 'lucide-react';
import type { Project } from '@/types';

interface StatItemProps {
  icon: React.ComponentType<{ className?: string }>;
  count: number;
  label: string;
  variant?: 'default' | 'success' | 'destructive';
}

function StatItem({ icon: Icon, count, label, variant = 'default' }: StatItemProps) {
  const getTextColor = () => {
    switch (variant) {
      case 'success':
        return 'text-success';
      case 'destructive':
        return 'text-destructive';
      default:
        return 'text-foreground';
    }
  };

  const getIconColor = () => {
    switch (variant) {
      case 'success':
        return 'text-success';
      case 'destructive':
        return 'text-destructive';
      default:
        return 'text-muted-foreground';
    }
  };

  return (
    <div className="text-center">
      <div className={`flex items-center justify-center gap-1 text-lg font-bold ${getTextColor()}`}>
        <Icon className={`h-4 w-4 ${getIconColor()}`} />
        {count}
      </div>
      <div className="text-xs text-muted-foreground">{label}</div>
    </div>
  );
}

interface ProjectStatsDisplayProps {
  project: Project;
  variant?: 'compact' | 'detailed';
  className?: string;
}

/**
 * Shared component for displaying project statistics (modules, deployed, failed).
 * Used in both ProjectCard and ProjectDetail to ensure consistent display logic.
 *
 * @param project - Project data with module counts
 * @param variant - Display variant (compact or detailed)
 * @param className - Optional additional CSS classes
 */
export function ProjectStatsDisplay({ project, variant = 'compact', className = '' }: ProjectStatsDisplayProps) {
  const baseClasses = variant === 'compact'
    ? 'grid grid-cols-3 gap-4 p-4 bg-muted/50 rounded-md'
    : 'grid grid-cols-3 gap-6';

  return (
    <div className={`${baseClasses} ${className}`}>
      <StatItem
        icon={Package}
        count={project.module_count || 0}
        label="Modules"
        variant="default"
      />
      <StatItem
        icon={CheckCircle2}
        count={project.deployed_count || 0}
        label="Deployed"
        variant="success"
      />
      <StatItem
        icon={XCircle}
        count={project.failed_count || 0}
        label="Failed"
        variant="destructive"
      />
    </div>
  );
}
