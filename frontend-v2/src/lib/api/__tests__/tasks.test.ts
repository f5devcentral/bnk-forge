/**
 * FU-024: lib/api/tasks — task API calls
 */
import { describe, it, expect } from 'vitest';
import { tasksApi } from '../tasks';

describe('tasksApi', () => {
  it('getTasks returns task list', async () => {
    const result = await tasksApi.getTasks();
    expect(result).toHaveProperty('tasks');
    expect(Array.isArray(result.tasks)).toBe(true);
  });

  it('getTask returns single task with expected properties', async () => {
    const task = await tasksApi.getTask(1);
    expect(task).toHaveProperty('id');
    expect(task).toHaveProperty('task_type');
    expect(task).toHaveProperty('status');
  });

  it('getTaskStats returns stats summary', async () => {
    const stats = await tasksApi.getTaskStats();
    expect(stats).toBeDefined();
  });

  it('cancelTask returns success', async () => {
    const result = await tasksApi.cancelTask(1);
    expect(result).toHaveProperty('success', true);
  });
});
