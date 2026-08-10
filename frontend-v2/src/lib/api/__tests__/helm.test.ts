/**
 * FU-021: lib/api/helm — Helm API calls
 */
import { describe, it, expect } from 'vitest';
import { helmApi } from '../helm';

describe('helmApi', () => {
  it('listHelmReleases returns release data', async () => {
    // API does .then(res => res.data) — handler returns plain array
    const releases = await helmApi.listHelmReleases(1);
    expect(Array.isArray(releases)).toBe(true);
    expect(releases.length).toBeGreaterThan(0);
    expect(releases[0]).toHaveProperty('name');
  });

  it('listHelmRepositories returns repo data', async () => {
    // API does .then(res => res.data) — handler returns plain array
    const repos = await helmApi.listHelmRepositories();
    expect(Array.isArray(repos)).toBe(true);
    expect(repos.length).toBeGreaterThan(0);
  });

  it('searchHelmCharts returns results', async () => {
    const results = await helmApi.searchHelmCharts('nginx');
    expect(Array.isArray(results)).toBe(true);
  });

  it('browseHelmCharts returns chart list', async () => {
    const charts = await helmApi.browseHelmCharts();
    expect(Array.isArray(charts)).toBe(true);
    expect(charts.length).toBeGreaterThan(0);
  });
});
