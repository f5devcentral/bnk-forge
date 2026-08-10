import { z } from 'zod';

const ROLES = ['admin', 'operator', 'viewer'] as const;

export const createUserSchema = z.object({
  username: z
    .string()
    .min(1, 'Username is required')
    .max(64, 'Username must be at most 64 characters')
    .regex(/^[A-Za-z0-9._-]+$/, 'Use letters, numbers, dots, underscores, or hyphens'),
  email: z.string().min(1, 'Email is required').email('Enter a valid email address'),
  password: z.string().min(8, 'Password must be at least 8 characters'),
  role: z.enum(ROLES, { required_error: 'Select a role' }),
});

export type CreateUserFormData = z.infer<typeof createUserSchema>;

export const updateUserSchema = z.object({
  email: z.string().email('Enter a valid email address').optional().or(z.literal('')),
  role: z.enum(ROLES).optional(),
  is_active: z.boolean().optional(),
});

export type UpdateUserFormData = z.infer<typeof updateUserSchema>;
