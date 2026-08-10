import { z } from 'zod';

const CHANNEL_TYPES = ['webhook', 'slack', 'msteams', 'email'] as const;
const EVENT_TYPES = [
  'health_change',
  'drift_detected',
  'deploy_success',
  'deploy_failed',
  'deploy_started',
] as const;

export const alertChannelSchema = z
  .object({
    name: z.string().min(1, 'Name is required').max(120, 'Name must be at most 120 characters'),
    channel_type: z.enum(CHANNEL_TYPES).default('webhook'),
    url: z.string().optional().or(z.literal('')),
    description: z
      .string()
      .max(500, 'Description must be at most 500 characters')
      .optional()
      .or(z.literal('')),
    event_types: z.array(z.enum(EVENT_TYPES)).default([]),
    min_interval_seconds: z.number().int().min(0, 'Must be 0 or greater').default(60),
    enabled: z.boolean().default(true),
  })
  .superRefine((data, ctx) => {
    // URL is required for every channel type except email
    if (data.channel_type !== 'email' && !data.url) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'Webhook URL is required',
        path: ['url'],
      });
    }
    if (data.url && !/^https?:\/\//i.test(data.url)) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: 'URL must start with http:// or https://',
        path: ['url'],
      });
    }
  });

export type AlertChannelFormData = z.infer<typeof alertChannelSchema>;
