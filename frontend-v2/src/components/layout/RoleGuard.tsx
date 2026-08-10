/**
 * Role-based UI guard.
 * 
 * Conditionally renders children based on the current user's role.
 * Use for hiding admin-only sections, disabling mutation buttons for viewers, etc.
 * 
 * Usage:
 *   <RoleGuard require="admin">
 *     <AdminOnlyContent />
 *   </RoleGuard>
 * 
 *   <RoleGuard require="operator" fallback={<Badge>Read Only</Badge>}>
 *     <DeployButton />
 *   </RoleGuard>
 */
import { useRole } from '@/hooks/useRole';
import type { UserRole } from '@/types';

interface RoleGuardProps {
  /** Minimum role required to see children */
  require: UserRole;
  /** Content to show if role check fails (default: nothing) */
  fallback?: React.ReactNode;
  children: React.ReactNode;
}

export function RoleGuard({ require, fallback = null, children }: RoleGuardProps) {
  const { hasMinRole } = useRole();

  if (!hasMinRole(require)) {
    return <>{fallback}</>;
  }

  return <>{children}</>;
}
