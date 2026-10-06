/**
 * FU-009: lib/queryKeys — query key factory structure
 */
import { describe, it, expect } from 'vitest';
import { keepPreviousForCluster, keepPreviousForScope, queryKeys } from '../queryKeys';

describe('queryKeys', () => {
  it('has all top-level domain keys', () => {
    const expectedDomains = [
      'projects', 'modules', 'tasks', 'drift', 'k8s',
      'deployments', 'helm', 'operators', 'fleet', 'stacks',
      'snapshots', 'system', 'registry', 'secrets', 'moduleSources',
      'notifications', 'auth', 'configPromotion', 'runbooks', 'execution',
      'dashboard', 'driftExtended', 'sshCredentials',
    ];
    for (const domain of expectedDomains) {
      expect(queryKeys).toHaveProperty(domain);
    }
  });

  describe('keys are readonly arrays', () => {
    it('projects.all is an array', () => {
      expect(Array.isArray(queryKeys.projects.all)).toBe(true);
      expect(queryKeys.projects.all).toEqual(['projects']);
    });

    it('tasks.all is an array', () => {
      expect(Array.isArray(queryKeys.tasks.all)).toBe(true);
      expect(queryKeys.tasks.all).toEqual(['tasks']);
    });
  });

  describe('parameterized keys include params', () => {
    it('projects.detail includes id', () => {
      expect(queryKeys.projects.detail(42)).toEqual(['projects', 'detail', 42]);
    });

    it('modules.library.list includes params', () => {
      const params = { category: 'networking', provider: 'aws' };
      expect(queryKeys.modules.library.list(params)).toEqual(['module-library', 'list', params]);
    });

    it('tasks.list includes filters', () => {
      const filters = { project_id: 1, status: 'completed' };
      expect(queryKeys.tasks.list(filters)).toEqual(['tasks', 'list', filters]);
    });

    it('k8s.clusters.resources includes all params', () => {
      const key = queryKeys.k8s.clusters.resources(1, 'pods', { namespace: 'default' });
      expect(key).toEqual(['k8s', 'clusters', 1, 'resources', 'pods', { namespace: 'default' }]);
    });

    it('helm.releases.byCluster includes clusterId', () => {
      expect(queryKeys.helm.releases.byCluster(5, 'kube-system', false)).toEqual([
        'helm-releases', 5, 'kube-system', false,
      ]);
    });
  });

  describe('keys are unique across domains', () => {
    it('top-level all keys are distinct', () => {
      const allKeys = [
        queryKeys.projects.all[0],
        queryKeys.tasks.all[0],
        queryKeys.drift.all[0],
        queryKeys.k8s.all[0],
        queryKeys.helm.all[0],
        queryKeys.operators.all[0],
        queryKeys.fleet.all[0],
        queryKeys.stacks.all[0],
        queryKeys.system.all[0],
        queryKeys.auth.all[0],
      ];
      const uniqueKeys = new Set(allKeys);
      expect(uniqueKeys.size).toBe(allKeys.length);
    });
  });

  describe('keepPreviousForScope and keepPreviousForCluster helpers', () => {
    it('keepPreviousForCluster retains data when cluster id matches', () => {
      const helper = keepPreviousForCluster(42);
      const prevData = { count: 1 };
      const result = helper(prevData, { queryKey: ['cluster', 42, 'resources'] });
      expect(result).toBe(prevData);
    });

    it('keepPreviousForCluster discards data when cluster id differs', () => {
      const helper = keepPreviousForCluster(42);
      const prevData = { count: 1 };
      const result = helper(prevData, { queryKey: ['cluster', 99, 'resources'] });
      expect(result).toBeUndefined();
    });

    it('keepPreviousForScope retains data when all scope tokens match', () => {
      const helper = keepPreviousForScope(1, 'Gateway', 'default');
      const prevData = { items: ['gw-1'] };
      const result = helper(prevData, { queryKey: ['bnk-resources', 1, 'Gateway', 'default'] });
      expect(result).toBe(prevData);
    });

    it('keepPreviousForScope discards data when namespace differs', () => {
      const helper = keepPreviousForScope(1, 'Gateway', 'staging');
      const prevData = { items: ['gw-1'] };
      // Previous query was from 'prod', current scope expects 'staging'
      const result = helper(prevData, { queryKey: ['bnk-resources', 1, 'Gateway', 'prod'] });
      expect(result).toBeUndefined();
    });

    it('keepPreviousForScope discards data when resource type differs', () => {
      const helper = keepPreviousForScope(1, 'HTTPRoute', 'default');
      const prevData = { items: ['gw-1'] };
      const result = helper(prevData, { queryKey: ['bnk-resources', 1, 'Gateway', 'default'] });
      expect(result).toBeUndefined();
    });
  });
});
