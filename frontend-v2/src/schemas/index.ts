/**
 * Zod validation schemas (IMP-021).
 *
 * Each domain has its own schema file. Import from here for convenience:
 *   import { createProjectSchema } from '@/schemas';
 */
export { createProjectSchema, type CreateProjectFormData } from './project';
export { changePasswordSchema, type ChangePasswordFormData } from './auth';
export {
  createUserSchema,
  updateUserSchema,
  type CreateUserFormData,
  type UpdateUserFormData,
} from './user';
export { blueprintSourceSchema, type BlueprintSourceFormData } from './blueprint';
export { moduleSourceSchema, type ModuleSourceFormData } from './moduleSource';
export { variableMappingSchema, type VariableMappingFormData } from './variableMapping';
export { alertChannelSchema, type AlertChannelFormData } from './alertChannel';
