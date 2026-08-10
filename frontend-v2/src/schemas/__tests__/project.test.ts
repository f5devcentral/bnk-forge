/**
 * FU-016: schemas/project — Zod schema validation
 */
import { describe, it, expect } from 'vitest';
import { createProjectSchema } from '../project';

describe('createProjectSchema', () => {
  const validProject = {
    name: 'my-project',
    description: 'A test project',
    project_type: 'cloud-aws' as const,
    environment: 'dev' as const,
    region: 'us-east-1',
    backend_type: 'local' as const,
    color: '#2563eb',
  };

  it('validates a correct project', () => {
    const result = createProjectSchema.safeParse(validProject);
    expect(result.success).toBe(true);
  });

  it('rejects missing name', () => {
    const result = createProjectSchema.safeParse({ ...validProject, name: '' });
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error.issues.some((i) => i.path.includes('name'))).toBe(true);
    }
  });

  it('rejects name with invalid chars', () => {
    const result = createProjectSchema.safeParse({ ...validProject, name: '#invalid!' });
    expect(result.success).toBe(false);
  });

  it('rejects name starting with special char', () => {
    const result = createProjectSchema.safeParse({ ...validProject, name: '-starts-with-dash' });
    expect(result.success).toBe(false);
  });

  it('accepts name with spaces, hyphens, underscores', () => {
    const result = createProjectSchema.safeParse({ ...validProject, name: 'My Project_v1-test' });
    expect(result.success).toBe(true);
  });

  it('rejects name exceeding 100 chars', () => {
    const result = createProjectSchema.safeParse({ ...validProject, name: 'a'.repeat(101) });
    expect(result.success).toBe(false);
  });

  it('rejects invalid project type', () => {
    const result = createProjectSchema.safeParse({ ...validProject, project_type: 'invalid' });
    expect(result.success).toBe(false);
  });

  it('validates cloud-aws project type', () => {
    const result = createProjectSchema.safeParse({
      ...validProject,
      project_type: 'cloud-aws',
      region: 'us-east-1',
    });
    expect(result.success).toBe(true);
  });

  it('validates kubernetes project type', () => {
    const result = createProjectSchema.safeParse({
      ...validProject,
      project_type: 'kubernetes',
      region: '',
    });
    expect(result.success).toBe(true);
  });

  it('validates cloud-ibm project type', () => {
    const result = createProjectSchema.safeParse({
      ...validProject,
      project_type: 'cloud-ibm',
      region: 'us-south',
    });
    expect(result.success).toBe(true);
  });

  it('requires region for cloud projects (cross-field validation)', () => {
    const result = createProjectSchema.safeParse({
      ...validProject,
      project_type: 'cloud-aws',
      region: '',
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error.issues.some((i) => i.path.includes('region'))).toBe(true);
    }
  });

  it('requires s3_state_bucket when backend_type is s3', () => {
    const result = createProjectSchema.safeParse({
      ...validProject,
      backend_type: 's3',
      s3_state_bucket: '',
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(result.error.issues.some((i) => i.path.includes('s3_state_bucket'))).toBe(true);
    }
  });

  it('accepts s3 backend with bucket specified', () => {
    const result = createProjectSchema.safeParse({
      ...validProject,
      backend_type: 's3',
      s3_state_bucket: 'my-state-bucket',
    });
    expect(result.success).toBe(true);
  });

  it('defaults environment to dev', () => {
    const { environment: _environment, ...withoutEnv } = validProject;
    const result = createProjectSchema.safeParse(withoutEnv);
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.environment).toBe('dev');
    }
  });

  it('rejects description over 500 chars', () => {
    const result = createProjectSchema.safeParse({
      ...validProject,
      description: 'x'.repeat(501),
    });
    expect(result.success).toBe(false);
  });
});
