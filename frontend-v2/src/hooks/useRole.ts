/**
 * Role-based access control hook.
 * 
 * Provides role checks for the current authenticated user.
 * Roles hierarchy: admin > operator > viewer
 */
import { useAuthStore } from '@/stores/authStore';
import type { UserRole } from '@/types';

const ROLE_HIERARCHY: Record<UserRole, number> = {
  admin: 3,
  operator: 2,
  viewer: 1,
};

export function useRole() {
  const user = useAuthStore((s) => s.user);
  const role = (user?.role ?? 'viewer') as UserRole;

  return {
    role,
    isAdmin: role === 'admin',
    isOperator: role === 'admin' || role === 'operator',
    isViewer: true, // everyone authenticated is at least viewer

    /** Check if user has one of the allowed roles */
    hasRole: (...allowed: UserRole[]) => allowed.includes(role),

    /** Check if user's role level is >= the required role */
    hasMinRole: (minRole: UserRole) =>
      ROLE_HIERARCHY[role] >= ROLE_HIERARCHY[minRole],

    /** Get display label for current role */
    roleLabel: role.charAt(0).toUpperCase() + role.slice(1),
  };
}
