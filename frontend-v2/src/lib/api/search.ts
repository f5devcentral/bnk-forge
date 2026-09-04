/**
 * Global Search API methods
 */
import { apiClient } from './client';
import type { GlobalSearchResponse } from '@/types/search';

export async function searchGlobal(q: string, limit = 25): Promise<GlobalSearchResponse> {
  const params = new URLSearchParams({ q, limit: limit.toString() });
  const response = await apiClient.get<GlobalSearchResponse>(`/api/k8s/search?${params.toString()}`);
  return response.data;
}
