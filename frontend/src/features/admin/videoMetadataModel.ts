import type { ArchiveVideoMetadataItem } from '../../types/api';

export type MetadataStatus = 'published' | 'hidden';
export type PersonFormState = {
  display_name: string;
  slug: string;
  aliases: string;
  description: string;
  status: MetadataStatus;
  sort_order: string;
};
export type TagFormState = {
  label: string;
  slug: string;
  kind: string;
  description: string;
  status: MetadataStatus;
  sort_order: string;
};

export const emptyPersonForm: PersonFormState = {
  display_name: '',
  slug: '',
  aliases: '',
  description: '',
  status: 'published',
  sort_order: '',
};
export const emptyTagForm: TagFormState = {
  label: '',
  slug: '',
  kind: 'category',
  description: '',
  status: 'published',
  sort_order: '',
};
export const tagKindOptions = ['category', 'topic', 'label', 'group'];

export function normalizeItems<T>(value: T[] | { items?: T[] } | null | undefined): T[] {
  return Array.isArray(value) ? value : (value?.items ?? []);
}
export function parseAliases(raw: string) {
  return raw
    .split(',')
    .map((part) => part.trim())
    .filter(Boolean);
}
export function parseSortOrder(raw: string) {
  if (!raw.trim()) return undefined;
  const value = Number(raw);
  return Number.isNaN(value) ? null : value;
}
export function asArchiveVideo(
  item:
    | ArchiveVideoMetadataItem
    | { video?: ArchiveVideoMetadataItem; item?: ArchiveVideoMetadataItem }
    | null
    | undefined
) {
  if (!item) return null;
  if ('video' in item && item.video) return item.video;
  if ('item' in item && item.item) return item.item;
  return item as ArchiveVideoMetadataItem;
}
export function buildSelectionMap<T extends { slug: string }>(items: T[] | undefined) {
  return Object.fromEntries((items ?? []).map((item) => [item.slug, true]));
}
