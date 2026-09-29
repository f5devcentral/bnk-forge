import { useQuery } from '@tanstack/react-query';
import { searchGlobal } from '@/lib/api';
import { queryKeys } from '@/lib/queryKeys';
import { useDebounce } from '@/hooks/useDebounce';
import type { GlobalSearchResponse } from '@/types/search';

/** Shortest query that triggers the live multi-cluster scan. */
export const MIN_SEARCH_LENGTH = 3;

export function useGlobalSearch(query: string, limit = 25, debounceMs = 400) {
  const debouncedQuery = useDebounce(query.trim(), debounceMs);
  const isEnabled = debouncedQuery.length >= MIN_SEARCH_LENGTH;

  const queryResult = useQuery<GlobalSearchResponse>({
    queryKey: queryKeys.search.query(debouncedQuery),
    queryFn: ({ signal }) => searchGlobal(debouncedQuery, limit, signal),
    enabled: isEnabled,
    staleTime: 10_000,
  });

  return {
    ...queryResult,
    debouncedQuery,
    isSearching: (query.trim().length >= MIN_SEARCH_LENGTH && debouncedQuery !== query.trim()) || queryResult.isLoading || queryResult.isFetching,
  };
}
