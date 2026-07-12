import { beforeEach, describe, expect, it, vi } from 'vitest';

const storage = new Map<string, string>();
vi.stubGlobal('localStorage', {
  getItem: (key: string) => storage.get(key) ?? null,
  setItem: (key: string, value: string) => storage.set(key, value),
});
vi.stubGlobal('crypto', { randomUUID: () => 'local-id' });

import { localSavedSearches } from '../services/savedSearches';

describe('local saved searches', () => {
  beforeEach(() => {
    for (const item of localSavedSearches.list()) localSavedSearches.remove(item.id);
    storage.clear();
  });

  it('persists complete filters and removes only after an explicit request', () => {
    const item = localSavedSearches.add('rent', {
      source: 'youtube',
      category: 'news',
      min_duration: 120,
      sort_by: 'date_desc',
    });
    expect(localSavedSearches.list()).toEqual([item]);
    expect(storage.get('saved-searches:v1')).toContain('date_desc');

    localSavedSearches.remove(item.id);
    expect(localSavedSearches.list()).toEqual([]);
  });
});
