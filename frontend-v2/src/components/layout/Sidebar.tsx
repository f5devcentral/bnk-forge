import { NavLink, Link, useMatch, useResolvedPath, useNavigate } from 'react-router-dom';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { BrandLogo } from '@/components/branding/BrandLogo';

import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import {
  LayoutDashboard,
  FolderGit2,
  Package,
  ScrollText,
  ChevronLeft,
  ChevronRight,
  ChevronDown,
  Server,
  Box,
  Shield,
  ShieldCheck,
  Layers,
  KeyRound,
  LogOut,
  // K8S-UX-005: Radio removed — Operators entry merged into Fleet
  GitCompare,
  Globe,
  Users,
  BarChart3,
  Bot,
  HardDrive,
  Activity,
} from 'lucide-react';
import { useProjects } from '@/hooks/useProjects';
import { useTaskStats, useTasks } from '@/hooks/useTasks';
import { useAllClusters } from '@/hooks/useK8s';
import { useFleetHealth } from '@/hooks/useFleet';
import { useGlobalDriftCount } from '@/hooks/useDrift';
import * as React from 'react';
import { startTransition, useState } from 'react';
import { useAuthStore } from '@/stores/authStore';
import type { UserRole } from '@/types';
import { ChangePasswordDialog } from '@/components/auth/ChangePasswordDialog';

// Role hierarchy for filtering
const ROLE_LEVEL: Record<UserRole, number> = { admin: 3, operator: 2, viewer: 1 };

// Navigation item type
interface NavItem {
  name: string;
  href: string;
  icon: React.ElementType;
  showCount?: string;
  minRole?: UserRole;
  showDriftBadge?: boolean;
}

// Workflow-First Navigation Structure
const navigationSections: {
  label: string;
  minRole?: UserRole;
  items: NavItem[];
}[] = [
  {
    // Standalone home item (no section header) — sits above OBSERVE.
    label: '',
    items: [{ name: 'Command Center', href: '/', icon: LayoutDashboard }],
  },
  {
    label: 'OBSERVE',
    items: [
      { name: 'Dashboard', href: '/observability/ai-gateway', icon: Activity, minRole: 'viewer' },
      { name: 'LLM Logs', href: '/observability/ai-gateway/logs', icon: ScrollText, minRole: 'viewer' },
    ],
  },
  {
    label: 'BUILD',
    items: [
      { name: 'Catalog', href: '/catalog', icon: Package },
      { name: 'Blueprints', href: '/stacks', icon: Layers },
      { name: 'Access Methods', href: '/auth-templates', icon: KeyRound, minRole: 'operator' },
      { name: 'Projects', href: '/projects', icon: FolderGit2, showCount: 'projects', showDriftBadge: true },
      // K8S-UX-006: Operations Log moved under Build workflow
      { name: 'Operations Log', href: '/tasks', icon: ScrollText, showCount: 'activity' },
    ],
  },
  {
    label: 'OPERATE',
    items: [
      { name: 'Fleet', href: '/fleet', icon: Globe, showCount: 'fleet' },
      // D-022 P6 IA: Infrastructure section — DPU/BlueField + bare-metal hosts
      { name: 'Infrastructure', href: '/infrastructure', icon: HardDrive },
      { name: 'Kubernetes', href: '/kubernetes', icon: Box, showCount: 'clusters' },
      { name: 'F5 BNK', href: '/bnk', icon: Shield },
      { name: 'WAF Policies', href: '/waf-policies', icon: ShieldCheck },
      { name: 'WAF Dashboard', href: '/waf-dashboard', icon: BarChart3 },
      { name: 'CNF Resources', href: '/cnf', icon: Layers },
      { name: 'Benchmarks', href: '/benchmarks', icon: BarChart3 },
      { name: 'MCP Server', href: '/mcp-server', icon: Bot },
    ],
  },
  {
    label: 'SETTINGS',
    minRole: 'operator',
    items: [
      { name: 'Users', href: '/users', icon: Users, minRole: 'admin' },
      { name: 'System', href: '/system', icon: Server, minRole: 'admin' },
    ],
  },
];

// v2: defaults now expand every section (previously CATALOG started
// collapsed). Bumped key to blow away any stale saved state.
const SECTION_STATE_STORAGE_KEY = 'sidebar.sectionCollapsed.v2';

// All sections default to expanded. The per-section collapsed state is
// persisted to localStorage so an operator's choice (collapse CATALOG,
// keep everything else open, etc.) sticks across reloads.
const DEFAULT_SECTION_STATE: Record<string, boolean> = {};

function loadSectionState(): Record<string, boolean> {
  try {
    const raw = window.localStorage.getItem(SECTION_STATE_STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === 'object') {
        return { ...DEFAULT_SECTION_STATE, ...parsed };
      }
    }
  } catch {
    // localStorage disabled / malformed — fall through.
  }
  return { ...DEFAULT_SECTION_STATE };
}

export function Sidebar() {
  const [collapsed, setCollapsed] = useState(false);
  const [sectionCollapsed, setSectionCollapsed] = useState<Record<string, boolean>>(loadSectionState);

  const toggleSection = (label: string) => {
    setSectionCollapsed((prev) => {
      const next = { ...prev, [label]: !prev[label] };
      try {
        window.localStorage.setItem(SECTION_STATE_STORAGE_KEY, JSON.stringify(next));
      } catch {
        // ignore quota / disabled storage
      }
      return next;
    });
  };
  const user = useAuthStore((s) => s.user);
  const { data: projects } = useProjects();
  const { data: taskStats } = useTaskStats({ days: 1 });
  const { data: latestTaskData } = useTasks({ limit: 1 }); // Get latest task for status indicator
  const { data: clustersData } = useAllClusters();
  const { data: fleetHealth } = useFleetHealth();
  const driftCount = useGlobalDriftCount();

  const projectCount = projects?.length ?? 0;
  const inProgressTasks = (taskStats?.by_status?.in_progress ?? 0) + (taskStats?.by_status?.queued ?? 0);
  const clusterCount = clustersData?.count ?? 0;
  const fleetTotal = fleetHealth?.total_clusters ?? 0;

  // Get latest task status for Activity indicator
  const latestTask = latestTaskData?.tasks?.[0];
  const latestTaskStatus = latestTask?.status;

  // Get count for navigation items
  const getCount = (countType?: string) => {
    switch (countType) {
      case 'projects':
        return projectCount;
      case 'activity':
        return inProgressTasks;
      case 'clusters':
        return clusterCount;
      case 'fleet':
        return fleetTotal;
      default:
        return null;
    }
  };

  const UserFooter = () => {
    const { user, logout } = useAuthStore();
    const navigate = useNavigate();
    const [showChangePassword, setShowChangePassword] = useState(false);

    if (!user) return null;

    return (
      <>
        <div className="flex items-center justify-between py-1">
          <div className="flex items-center gap-2 text-xs text-muted-foreground min-w-0">
            <div className="h-6 w-6 rounded-full bg-primary/20 flex items-center justify-center flex-shrink-0">
              <span className="text-[10px] font-medium text-primary">
                {user.username.charAt(0).toUpperCase()}
              </span>
            </div>
            <span className="truncate">{user.username}</span>
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground">{user.role}</span>
          </div>
          <div className="flex items-center gap-0.5">
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-6 w-6 text-muted-foreground hover:text-primary"
                  onClick={() => setShowChangePassword(true)}
                  aria-label="Change password"
                >
                  <KeyRound className="h-3.5 w-3.5" />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="top">Change password</TooltipContent>
            </Tooltip>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-6 w-6 text-muted-foreground hover:text-destructive"
                  onClick={() => {
                    logout();
                    // startTransition avoids React #426 — Login is lazy-loaded and
                    // synchronous navigation during user input causes suspension errors
                    startTransition(() => navigate('/login'));
                  }}
                  aria-label="Logout"
                >
                  <LogOut className="h-3.5 w-3.5" />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="top">Log out</TooltipContent>
            </Tooltip>
          </div>
        </div>
        <ChangePasswordDialog open={showChangePassword} onOpenChange={setShowChangePassword} />
      </>
    );
  };

  return (
    <TooltipProvider>
      <aside aria-label="Application sidebar" className={cn(
        'border-r border-border bg-card flex flex-col h-screen transition-all duration-300',
        collapsed ? 'w-16' : 'w-64',
      )}>
        {/* Logo and Header */}
        <div className={cn(
          'border-b border-border flex items-center',
          collapsed ? 'justify-center py-3' : 'p-6 justify-between',
        )}>
          {!collapsed ? (
            <Link to="/" className="flex items-center gap-3">
              <BrandLogo className="w-9 h-9 flex-shrink-0 text-foreground" aria-hidden="true" />
              <div>
                <h1 className="text-lg font-bold tracking-tight text-foreground">
                  BNK Forge
                </h1>
                <p className="text-[10px] uppercase tracking-widest text-muted-foreground">
                  Infrastructure
                </p>
              </div>
            </Link>
          ) : (
            <Link to="/" aria-label="Home">
              <BrandLogo className="w-9 h-9 text-foreground" aria-hidden="true" />
            </Link>
          )}
        </div>

        {/* Navigation */}
        <nav className={cn(
          'flex-1 overflow-y-auto',
          collapsed ? 'py-3 px-1' : 'p-3'
        )} aria-label="Main navigation">
          {navigationSections
            .filter((section) => {
              if (!section.minRole) return true;
              const userLevel = ROLE_LEVEL[(user?.role as UserRole) ?? 'viewer'];
              return userLevel >= ROLE_LEVEL[section.minRole];
            })
            .map((section) => {
              const isSectionCollapsed = !collapsed && !!sectionCollapsed[section.label];
              return (
            <div key={section.label || section.items[0]?.name} className="mb-6">
              {!section.label ? null : !collapsed ? (
                <button
                  type="button"
                  onClick={() => toggleSection(section.label)}
                  className="w-full flex items-center justify-between px-3 mb-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground hover:text-foreground transition-colors"
                  aria-expanded={!isSectionCollapsed}
                >
                  <span>{section.label}</span>
                  <ChevronDown
                    className={cn(
                      'h-3 w-3 transition-transform',
                      isSectionCollapsed ? '-rotate-90' : 'rotate-0',
                    )}
                  />
                </button>
              ) : (
                // Invisible spacer that occupies the same vertical extent as
                // the section header (text-[10px] line-height + mb-2 = ~22px)
                // so collapsed icons land at the same y-coordinate as their
                // expanded counterparts.
                <div className="h-[22px] mb-2" aria-hidden="true" />
              )}
              {!isSectionCollapsed && (
              <div className={cn('space-y-1', collapsed && 'flex flex-col items-center')}>
                {section.items
                  .filter((item) => {
                    if (!item.minRole) return true;
                    const userLevel = ROLE_LEVEL[(user?.role as UserRole) ?? 'viewer'];
                    return userLevel >= ROLE_LEVEL[item.minRole];
                  })
                  .map((item) => {
                  const count = getCount(item.showCount);

                  const link = (
                    <SidebarNavItem
                      key={item.name}
                      item={item}
                      collapsed={collapsed}
                      count={count}
                      driftCount={driftCount}
                      latestTaskStatus={latestTaskStatus}
                    />
                  );

                  if (collapsed) {
                    return (
                      <Tooltip key={item.name} delayDuration={0}>
                        <TooltipTrigger asChild>{link}</TooltipTrigger>
                        <TooltipContent side="right">{item.name}</TooltipContent>
                      </Tooltip>
                    );
                  }

                  return link;
                })}
              </div>
              )}
            </div>
              );
            })}
        </nav>

        {/* Footer with system status */}
        <div className="p-4 border-t border-border">
          {!collapsed ? (
            <div className="space-y-2">
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <div className="h-2 w-2 rounded-full bg-success animate-pulse" />
                <span>All systems operational</span>
              </div>
              {/* User info + logout */}
              <UserFooter />
              <div className="flex items-center justify-between text-xs text-muted-foreground">
                <span>Version {__APP_VERSION__}</span>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-6 w-6"
                  onClick={() => setCollapsed(true)}
                  aria-label="Collapse sidebar"
                >
                  <ChevronLeft className="h-4 w-4" aria-hidden="true" />
                </Button>
              </div>
            </div>
          ) : (
            <Button
              variant="ghost"
              size="icon"
              className="w-full"
              onClick={() => setCollapsed(false)}
              aria-label="Expand sidebar"
            >
              <ChevronRight className="h-4 w-4" aria-hidden="true" />
            </Button>
          )}
        </div>
      </aside>
    </TooltipProvider>
  );
}

// =====================================================================
// SidebarNavItem
// =====================================================================
//
// One row of the sidebar nav. Extracted so we can call useMatch /
// useResolvedPath at the top level (Rules of Hooks) and pass a STRING
// className down to NavLink instead of the function form.
//
// Why this matters: in collapsed mode the link is wrapped in
// <TooltipTrigger asChild>, whose Radix Slot merges props by string
// concatenation. A function className gets toString()-ed into the
// `class` attribute, the browser then parses every word inside the
// function source as a Tailwind class, and you get the wrong colors
// and hover state. Passing a precomputed string sidesteps that
// entirely.

interface SidebarNavItemProps {
  item: NavItem;
  collapsed: boolean;
  count: number | null;
  driftCount: number;
  latestTaskStatus: string | null | undefined;
}

const SidebarNavItem = React.forwardRef<HTMLAnchorElement, SidebarNavItemProps>(
  ({ item, collapsed, count, driftCount, latestTaskStatus, ...rest }, ref) => {
    const resolved = useResolvedPath(item.href);
    const match = useMatch({ path: resolved.pathname, end: item.href === '/' });
    const isActive = !!match;

    const linkClassName = cn(
      'flex items-center py-2.5 rounded-lg text-sm font-medium transition-all relative',
      collapsed ? 'w-10 mx-auto justify-center' : 'gap-3 px-3',
      isActive
        ? 'bg-primary/10 text-primary font-medium'
        : 'text-muted-foreground hover:bg-muted/50 hover:text-foreground'
    );

    return (
      <NavLink
        to={item.href}
        end={item.href === '/'}
        className={linkClassName}
        ref={ref}
        {...rest}
      >
        <item.icon className="h-4 w-4 flex-shrink-0" />

        <span className={cn('flex-1 text-left', collapsed && 'sr-only')}>{item.name}</span>

        {!collapsed && (
          <>
            {/* Drift badge — amber/red indicator next to Projects */}
            {item.showDriftBadge && driftCount > 0 && (
              <span
                className={cn(
                  'px-1.5 py-0.5 text-[10px] font-semibold rounded-full flex items-center gap-1',
                  driftCount >= 5
                    ? 'bg-destructive/10 text-destructive'
                    : 'bg-warning/10 text-warning'
                )}
                title={`${driftCount} module${driftCount !== 1 ? 's' : ''} with drift`}
              >
                <GitCompare className="h-3 w-3" />
                {driftCount}
              </span>
            )}
            {/* Count badge */}
            {count !== null && count > 0 && (
              <span className={cn(
                'px-1.5 py-0.5 text-xs rounded-full',
                isActive
                  ? 'bg-primary/15 text-primary'
                  : 'bg-muted text-muted-foreground'
              )}>
                {count}
              </span>
            )}
          </>
        )}

        {/* Drift indicator dot (visible in collapsed mode too) */}
        {item.showDriftBadge && driftCount > 0 && collapsed && (
          <div
            className={cn(
              'absolute -top-1 -right-1 h-3 w-3 rounded-full border-2 border-card',
              driftCount >= 5 ? 'bg-destructive' : 'bg-warning'
            )}
            title={`${driftCount} module${driftCount !== 1 ? 's' : ''} with drift`}
          />
        )}

        {/* Latest task status indicator */}
        {item.href === '/tasks' && latestTaskStatus && (
          <div
            className={cn(
              'absolute -top-1 -right-1 h-3 w-3 rounded-full border-2 border-card',
              latestTaskStatus === 'failed' && 'bg-destructive',
              latestTaskStatus === 'completed' && 'bg-success',
              (latestTaskStatus === 'in_progress' || latestTaskStatus === 'queued') && 'bg-primary animate-pulse'
            )}
            title={`Latest task: ${latestTaskStatus}`}
          />
        )}
      </NavLink>
    );
  }
);
SidebarNavItem.displayName = 'SidebarNavItem';
