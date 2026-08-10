// Authentication & User types

export type UserRole = 'admin' | 'operator' | 'viewer';

export interface User {
  id: number;
  username: string;
  email: string;
  role: UserRole;
  is_active: boolean;
  must_change_password: boolean;
  last_login_at: string | null;
  created_at: string;
}

/** MU-006: User with project ownership count */
export interface UserWithProjectCount extends User {
  project_count: number;
}

/** MU-005: Fields that can be updated on a user */
export interface UserUpdateData {
  email?: string;
  role?: UserRole;
  is_active?: boolean;
}

/** MU-005: Data for creating a new user */
export interface UserCreateData {
  username: string;
  email: string;
  password: string;
  role: UserRole;
}
