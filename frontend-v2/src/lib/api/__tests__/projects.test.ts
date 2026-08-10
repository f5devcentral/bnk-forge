/**
 * FU-019: lib/api/projects — project API calls
 */
import { describe, it, expect } from 'vitest';
import { projectsApi } from '../projects';

describe('projectsApi', () => {
  it('getProjects returns array of projects', async () => {
    const projects = await projectsApi.getProjects();
    expect(Array.isArray(projects)).toBe(true);
    expect(projects.length).toBeGreaterThan(0);
    expect(projects[0]).toHaveProperty('name');
    expect(projects[0]).toHaveProperty('id');
  });

  it('createProject returns success with project_id', async () => {
    const result = await projectsApi.createProject({
      name: 'Test Project',
      project_type: 'cloud-ibm',
      environment: 'dev',
      region: 'us-south',
    } as Parameters<typeof projectsApi.createProject>[0]);
    expect(result).toHaveProperty('success', true);
    expect(result).toHaveProperty('project_id');
  });

  it('getProjectModules returns array of modules', async () => {
    // API function unwraps: .then(res => res.data.modules)
    const modules = await projectsApi.getProjectModules(1);
    expect(Array.isArray(modules)).toBe(true);
  });

  it('getModuleLibrary returns array of library modules', async () => {
    // API function unwraps: .then(res => res.data.modules)
    const modules = await projectsApi.getModuleLibrary();
    expect(Array.isArray(modules)).toBe(true);
    expect(modules.length).toBeGreaterThan(0);
  });
});
