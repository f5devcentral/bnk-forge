/**
 * ResourceExplorerLayout — Compound component for sidebar + content + detail panel layouts.
 *
 * Used by: KubernetesV2, F5BNK, Stacks, Projects, TaskHistory, Modules.
 *
 * Provides:
 *   - ResourceExplorerLayout (root: full-height flex column)
 *   - ResourceExplorerLayout.Header (top bar, flex-shrink-0 with border)
 *   - ResourceExplorerLayout.Sidebar (left sidebar, configurable width)
 *   - ResourceExplorerLayout.Content (center scrollable area, flex-1)
 *   - ResourceExplorerLayout.DetailPanel (right panel, conditionally rendered)
 *   - SidebarFilterItem (reusable filter button for sidebar lists)
 *   - SidebarSection (titled section within sidebar)
 */

import { type ReactNode } from 'react';
import { useThemeClasses } from '@/context/ThemeContext';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';

// ============================================================================
// Root
// ============================================================================

interface ResourceExplorerLayoutProps {
  children: ReactNode;
  className?: string;
  /** data-onboarding attribute for walkthrough targets */
  'data-onboarding'?: string;
}

function Root({ children, className, ...props }: ResourceExplorerLayoutProps) {
  return (
    <div
      className={cn('h-screen flex flex-col bg-background', className)}
      {...props}
    >
      {children}
    </div>
  );
}

// ============================================================================
// Header
// ============================================================================

interface HeaderProps {
  children: ReactNode;
  className?: string;
}

function Header({ children, className }: HeaderProps) {
  const { borderDefault } = useThemeClasses();

  return (
    <div className={cn('flex-shrink-0 border-b', borderDefault, className)}>
      {children}
    </div>
  );
}

// ============================================================================
// Body — flex row container for sidebar + content + detail panel
// ============================================================================

interface BodyProps {
  children: ReactNode;
  className?: string;
}

function Body({ children, className }: BodyProps) {
  return (
    <div className={cn('flex-1 flex overflow-hidden', className)}>
      {children}
    </div>
  );
}

// ============================================================================
// Sidebar
// ============================================================================

interface SidebarProps {
  children: ReactNode;
  /** Tailwind width class. Default: 'w-56' */
  width?: string;
  className?: string;
}

function Sidebar({ children, width = 'w-56', className }: SidebarProps) {
  const { borderDefault } = useThemeClasses();

  return (
    <div
      className={cn(
        width,
        'flex-shrink-0 border-r overflow-y-auto bg-muted/30',
        borderDefault,
        className,
      )}
    >
      {children}
    </div>
  );
}

// ============================================================================
// Content
// ============================================================================

interface ContentProps {
  children: ReactNode;
  className?: string;
}

function Content({ children, className }: ContentProps) {
  return (
    <div className={cn('flex-1 overflow-auto', className)}>
      {children}
    </div>
  );
}

// ============================================================================
// DetailPanel
// ============================================================================

interface DetailPanelProps {
  children: ReactNode;
  /** Whether the panel is open. When false, renders nothing. */
  open: boolean;
  /** Tailwind width class. Default: 'w-96' */
  width?: string;
  className?: string;
}

function DetailPanel({ children, open, width = 'w-96', className }: DetailPanelProps) {
  const { borderDefault } = useThemeClasses();

  if (!open) return null;

  return (
    <div
      className={cn(
        width,
        'flex-shrink-0 border-l overflow-y-auto bg-card',
        borderDefault,
        className,
      )}
    >
      {children}
    </div>
  );
}

// ============================================================================
// SidebarSection — titled group within the sidebar
// ============================================================================

interface SidebarSectionProps {
  title: string;
  children: ReactNode;
  className?: string;
}

function SidebarSection({ title, children, className }: SidebarSectionProps) {
  return (
    <div className={cn('mt-4', className)}>
      <div className="text-xs font-medium uppercase tracking-wider px-3 mb-2 text-muted-foreground/70">
        {title}
      </div>
      {children}
    </div>
  );
}

// ============================================================================
// SidebarFilterItem — reusable filter button
// ============================================================================

interface SidebarFilterItemProps {
  icon: React.ElementType;
  label: string;
  count?: number;
  selected: boolean;
  onClick: () => void;
  /** Icon color class when not selected. Default: 'text-current' */
  iconColor?: string;
  className?: string;
}

function SidebarFilterItem({
  icon: Icon,
  label,
  count,
  selected,
  onClick,
  iconColor,
  className,
}: SidebarFilterItemProps) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'w-full flex items-center justify-between px-3 py-2 rounded-md text-sm transition-colors mb-0.5 border-l-2',
        selected
          ? 'bg-primary/10 text-primary font-medium border-l-primary'
          : 'text-muted-foreground hover:text-foreground hover:bg-muted/50 border-l-transparent',
        className,
      )}
    >
      <div className="flex items-center gap-2">
        <Icon className={cn('h-4 w-4', selected ? 'text-primary' : iconColor)} />
        <span>{label}</span>
      </div>
      {count !== undefined && (
        <Badge variant={selected ? 'outline' : 'secondary'} className="text-xs">
          {count}
        </Badge>
      )}
    </button>
  );
}

// ============================================================================
// Compound Export
// ============================================================================

/**
 * ResourceExplorerLayout — compound component for master-detail pages.
 *
 * @example
 * ```tsx
 * <ResourceExplorerLayout data-onboarding="k8s-page">
 *   <ResourceExplorerLayout.Header>
 *     <ResourcePageHeader ... />
 *   </ResourceExplorerLayout.Header>
 *   <ResourceExplorerLayout.Body>
 *     <ResourceExplorerLayout.Sidebar width="w-56">
 *       <SidebarFilterItem icon={Layers} label="All" count={42} selected onClick={...} />
 *     </ResourceExplorerLayout.Sidebar>
 *     <ResourceExplorerLayout.Content>
 *       <ResourceTable ... />
 *     </ResourceExplorerLayout.Content>
 *     <ResourceExplorerLayout.DetailPanel open={!!selected} width="w-96">
 *       <DetailView ... />
 *     </ResourceExplorerLayout.DetailPanel>
 *   </ResourceExplorerLayout.Body>
 * </ResourceExplorerLayout>
 * ```
 */
const ResourceExplorerLayout = Object.assign(Root, {
  Header,
  Body,
  Sidebar,
  Content,
  DetailPanel,
  SidebarSection,
  SidebarFilterItem,
});

export { ResourceExplorerLayout, SidebarFilterItem, SidebarSection };
export type { SidebarFilterItemProps, SidebarSectionProps, DetailPanelProps };
