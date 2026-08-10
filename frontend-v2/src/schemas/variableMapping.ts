import { z } from 'zod';

export const variableMappingSchema = z.object({
  source_variable_name: z
    .string()
    .min(1, 'Source variable name is required')
    .regex(/^[A-Za-z_][A-Za-z0-9_]*$/, 'Use letters, digits, or underscores; cannot start with a digit'),
  target_variable_name: z
    .string()
    .min(1, 'Target variable name is required')
    .regex(/^[A-Za-z_][A-Za-z0-9_]*$/, 'Use letters, digits, or underscores; cannot start with a digit'),
  transform_type: z.string().default('none'),
  is_active: z.boolean().default(true),
  description: z.string().max(500, 'Description must be at most 500 characters').optional().or(z.literal('')),
});

export type VariableMappingFormData = z.infer<typeof variableMappingSchema>;
