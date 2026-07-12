import type { MentionExportItem } from '../types/api';

const KEY = 'hasanara.playback-queue.v1';

function read(): MentionExportItem[] {
  try {
    const value = JSON.parse(localStorage.getItem(KEY) ?? '[]');
    return Array.isArray(value) ? value : [];
  } catch {
    return [];
  }
}

function write(items: MentionExportItem[]) {
  localStorage.setItem(KEY, JSON.stringify(items));
}

export const playbackQueue = {
  list: read,
  replace(items: MentionExportItem[]) {
    write(items);
    return items;
  },
  remove(index: number) {
    const items = read().filter((_, itemIndex) => itemIndex !== index);
    write(items);
    return items;
  },
  clear() {
    write([]);
    return [] as MentionExportItem[];
  },
};
