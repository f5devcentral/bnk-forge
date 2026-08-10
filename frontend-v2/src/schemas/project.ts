/**
 * Zod schemas for Project forms (IMP-021).
 *
 * Each schema mirrors the corresponding TypeScript interface from @/types
 * and adds runtime validation that was previously handled by HTML `required`
 * attributes or not at all.
 */
import { z } from 'zod';

const PROJECT_TYPES = ['cloud-aws', 'cloud-azure', 'cloud-gcp', 'cloud-ibm', 'kubernetes'] as const;

export const createProjectSchema = z
  .object({
    name: z
      .string()
      .min(1, 'Project name is required')
      .max(100, 'Project name must be at most 100 characters')
      .regex(
        /^[a-zA-Z0-9][a-zA-Z0-9 _-]*$/,
        'Must start with a letter or number and contain only letters, numbers, spaces, hyphens, or underscores',
      ),
    description: z.string().max(500, 'Description must be at most 500 characters').optional().or(z.literal('')),
    project_type: z.enum(PROJECT_TYPES, { required_error: 'Select a project type' }),
    environment: z.enum(['dev', 'staging', 'prod']).default('dev'),
    region: z.string().optional().or(z.literal('')),
    backend_type: z.enum(['local', 's3']).default('local'),
    state_storage_provider: z.string().optional().or(z.literal('')),
    s3_state_bucket: z.string().optional().or(z.literal('')),
    s3_state_key_prefix: z.string().optional().or(z.literal('')),
    s3_state_region: z.string().optional().or(z.literal('')),
    s3_use_native_locking: z.boolean().default(true),
    s3_endpoint: z.string().optional().or(z.literal('')),
    color: z.string().default('#2563eb'),
    icon: z.string().optional().or(z.literal('')),
    credential_template_id: z.number().optional(),
  })
  .superRefine((data, ctx) => {
    // Cloud projects require a region
    if (data.project_type?.startsWith('cloud-') && !data.region) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'Region is required for cloud projects',
        path: ['region'],
      });
    }
    // S3 backend requires a bucket name
    if (data.backend_type === 's3' && !data.s3_state_bucket) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'S3 bucket name is required',
        path: ['s3_state_bucket'],
      });
    }
  });

export type CreateProjectFormData = z.infer<typeof createProjectSchema>;
