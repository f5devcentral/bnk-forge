import { describe, expect, it } from 'vitest';
import { blueprintCatalogApi } from '../blueprint-catalog';

describe('blueprintCatalogApi', () => {
  it('lists blueprint sources', async () => {
    const sources = await blueprintCatalogApi.getBlueprintSources();
    expect(Array.isArray(sources)).toBe(true);
  });

  it('lists blueprint releases', async () => {
    const releases = await blueprintCatalogApi.getBlueprintReleases();
    expect(Array.isArray(releases)).toBe(true);
    expect(releases[0]).toHaveProperty('blueprint_name');
  });

  it('imports a blueprint release', async () => {
    const release = await blueprintCatalogApi.importBlueprintRelease(32, { state_reason: 'test import' });
    expect(release.release_state).toBe('imported');
  });

  it('deletes a blueprint source', async () => {
    const result = await blueprintCatalogApi.deleteBlueprintSource(7);
    expect(result.success).toBe(true);
  });

  it('deletes a blueprint release', async () => {
    const result = await blueprintCatalogApi.deleteBlueprintRelease(31);
    expect(result.success).toBe(true);
  });

  it('sets release visibility off via PATCH /visibility', async () => {
    const release = await blueprintCatalogApi.setBlueprintReleaseVisibility(31, false);
    expect(release.is_active).toBe(false);
    expect(release.release_state).toBe('imported');
  });

  it('sets release visibility on via PATCH /visibility', async () => {
    const release = await blueprintCatalogApi.setBlueprintReleaseVisibility(31, true);
    expect(release.is_active).toBe(true);
  });
});
