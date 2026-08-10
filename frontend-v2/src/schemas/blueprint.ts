import { z } from 'zod';

export const blueprintSourceSchema = z.object({
  name: z.string().min(1, 'Name is required').max(120, 'Name must be at most 120 characters'),
  source_type: z.enum(['git', 'local', 'builtin']).default('git'),
  url: z
    .string()
    .min(1, 'Repository URL is required')
    .url('Enter a valid URL (https:// or git@…)')
    .or(
      z
        .string()
        .regex(
          /^(git@|ssh:\/\/)/,
          'Enter a valid URL (https:// or git@…)',
        ),
    ),
  branch: z.string().optional().or(z.literal('')),
  git_ref: z.string().optional().or(z.literal('')),
  is_active: z.boolean().default(true),
  description: z.string().max(500, 'Description must be at most 500 characters').optional().or(z.literal('')),
  make_default: z.boolean().default(false),
});

export type BlueprintSourceFormData = z.infer<typeof blueprintSourceSchema>;
