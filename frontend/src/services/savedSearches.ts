import type { SavedSearch, SavedSearchFilters } from '../types/api';

const KEY = 'saved-searches:v1';

function load(): SavedSearch[] {
  try {
    return JSON.parse(localStorage.getItem(KEY) ?? '[]') as SavedSearch[];
  } catch {
    return [];
  }
}

function save(items: SavedSearch[]) {
  try {
    localStorage.setItem(KEY, JSON.stringify(items));
  } catch {
    // The UI retains its current in-memory state if browser storage is unavailable.
  }
}

let cache = load();

export const localSavedSearches = {
  list: () => [...cache],
  add(query: string, filters: SavedSearchFilters) {
    const item: SavedSearch = {
      id: `local:${crypto.randomUUID()}`,
      query,
      filters,
      created_at: new Date().toISOString(),
    };
    cache = [item, ...cache];
    save(cache);
    return item;
  },
  remove(id: string) {
    cache = cache.filter((item) => item.id !== id);
    save(cache);
  },
};
