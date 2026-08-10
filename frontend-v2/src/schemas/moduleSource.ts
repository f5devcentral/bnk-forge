import { z } from 'zod';

export const moduleSourceSchema = z.object({
  name: z.string().min(1, 'Name is required').max(120, 'Name must be at most 120 characters'),
  source_type: z.enum(['git', 'registry', 'builtin']).default('git'),
  url: z.string().min(1, 'Repository URL is required'),
  branch: z.string().optional().or(z.literal('')),
  git_ref: z.string().optional().or(z.literal('')),
  auth_type: z.enum(['none', 'token', 'ssh', 'basic']).default('none'),
  auth_token: z.string().optional().or(z.literal('')),
  auto_sync: z.boolean().default(false),
  sync_interval_hours: z.number().int().positive().default(24),
  description: z.string().max(500, 'Description must be at most 500 characters').optional().or(z.literal('')),
  make_default: z.boolean().default(false),
});

export type ModuleSourceFormData = z.infer<typeof moduleSourceSchema>;
