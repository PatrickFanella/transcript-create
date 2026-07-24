import { useEffect, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api, track } from '../../services';
import type { ArchiveSearchFilters, GroupedSearchResponse, SearchHit } from '../../types/api';
import type { SearchFilters } from './filters';

type SearchResult =
  | { mode: 'grouped'; grouped: GroupedSearchResponse; flatHits: SearchHit[] }
  | { mode: 'flat'; grouped: null; flatHits: SearchHit[] };
const EMPTY_HITS: SearchHit[] = [];

export function useArchiveSearch(filters: SearchFilters) {
  const shouldFetch = Boolean(filters.q.trim());
  const activeFilters: ArchiveSearchFilters = useMemo(
    () => ({
      source: filters.source,
      category: filters.category,
      date_from: filters.date_from,
      date_to: filters.date_to,
      min_duration: filters.min_duration,
      max_duration: filters.max_duration,
      sort_by: filters.sort_by,
      video_id: filters.video_id,
      limit: filters.limit,
      offset: filters.offset,
    }),
    [filters]
  );
  const suggestions = useQuery({
    queryKey: ['search-suggestions', 'a', 10],
    queryFn: ({ signal }) => api.getSearchSuggestions('a', 10, signal),
  });
  const search = useQuery<SearchResult>({
    queryKey: ['search', filters.q.trim(), activeFilters],
    enabled: shouldFetch,
    queryFn: async ({ signal }) => {
      try {
        const grouped = await api.searchGrouped(filters.q.trim(), activeFilters, signal);
        return { mode: 'grouped', grouped, flatHits: [] };
      } catch (error) {
        if (signal.aborted) throw error;
        const response = await api.search(filters.q.trim(), activeFilters, signal);
        return { mode: 'flat', grouped: null, flatHits: response.hits ?? [] };
      }
    },
  });

  useEffect(() => {
    if (shouldFetch) track({ type: 'search', payload: { ...activeFilters } });
  }, [activeFilters, shouldFetch]);

  return {
    shouldFetch,
    suggestedSearches: suggestions.data?.suggestions ?? [],
    grouped: search.data?.grouped ?? null,
    flatHits: search.data?.flatHits ?? EMPTY_HITS,
    mode: search.data?.mode ?? null,
    loading: search.isFetching,
    queryError: search.isError ? 'Search failed. Try again.' : null,
  };
}
