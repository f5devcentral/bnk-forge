/**
 * FU-018: lib/api/auth — auth API calls
 */
import { describe, it, expect } from 'vitest';
import { authApi } from '../auth';

describe('authApi', () => {
  it('login returns token and user', async () => {
    // MSW handler expects username=admin, password=password
    const result = await authApi.login('admin', 'password');
    expect(result).toHaveProperty('token');
    expect(result).toHaveProperty('user');
    expect(result.user).toHaveProperty('username');
  });

  it('getMe returns user object', async () => {
    const user = await authApi.getMe();
    expect(user).toHaveProperty('username');
    expect(user).toHaveProperty('role');
  });

  it('changePassword returns success', async () => {
    const result = await authApi.changePassword('old-pass', 'new-pass');
    expect(result).toHaveProperty('success', true);
  });

  it('getUsers returns users with total count', async () => {
    const result = await authApi.getUsers();
    expect(result).toHaveProperty('users');
    expect(result).toHaveProperty('total');
    expect(Array.isArray(result.users)).toBe(true);
    expect(result.users.length).toBeGreaterThan(0);
    expect(result.users[0]).toHaveProperty('username');
  });
});
