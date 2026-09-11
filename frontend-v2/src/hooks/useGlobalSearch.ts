import { useQuery } from '@tanstack/react-query';
import { searchGlobal } from '@/lib/api';
import { queryKeys } from '@/lib/queryKeys';
import { useDebounce } from '@/hooks/useDebounce';
import type { GlobalSearchResponse } from '@/types/search';

export function useGlobalSearch(query: string, limit = 25, debounceMs = 250) {
  const debouncedQuery = useDebounce(query.trim(), debounceMs);
  const isEnabled = debouncedQuery.length >= 2;

  const queryResult = useQuery<GlobalSearchResponse>({
    queryKey: queryKeys.search.query(debouncedQuery),
    queryFn: () => searchGlobal(debouncedQuery, limit),
    enabled: isEnabled,
    staleTime: 10_000,
  });

  return {
    ...queryResult,
    debouncedQuery,
    isSearching: (query.trim().length >= 2 && debouncedQuery !== query.trim()) || queryResult.isLoading || queryResult.isFetching,
  };
}
