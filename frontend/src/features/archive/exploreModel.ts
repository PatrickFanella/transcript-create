import type { ArchiveEvidenceMoment, ArchivePeriodOption, VideoInfo } from '../../types/api';
import { buildTimestampLink } from './format';

export type PeriodKind =
  | 'latest'
  | 'month'
  | 'week'
  | 'event'
  | 'leadup'
  | 'fallout'
  | 'holiday'
  | 'anniversary'
  | 'date';
export const PERIOD_KIND_TABS: Array<{ kind: PeriodKind; label: string }> = [
  { kind: 'latest', label: 'Latest' },
  { kind: 'month', label: 'Months' },
  { kind: 'week', label: 'Weeks' },
  { kind: 'event', label: 'Events' },
  { kind: 'leadup', label: 'Leadups' },
  { kind: 'fallout', label: 'Fallout' },
  { kind: 'holiday', label: 'Holidays' },
  { kind: 'anniversary', label: 'Anniversaries' },
  { kind: 'date', label: 'Dates' },
];
export const PERIOD_OPTION_FETCH_LIMIT = 24;
export const topicHref = (label: string) => `/topics/${encodeURIComponent(label)}`;
export const facetHref = (label: string) => `/search?q=${encodeURIComponent(label)}`;
export const evidenceHref = (moment: ArchiveEvidenceMoment) =>
  buildTimestampLink(moment.video.id, moment.start_ms);

export function normalizePeriodKind(kind?: string | null): PeriodKind | null {
  if (!kind) return null;
  const value = kind.toLowerCase();
  if (value === 'latest' || value === 'all') return 'latest';
  return PERIOD_KIND_TABS.find((tab) => value.startsWith(tab.kind))?.kind ?? null;
}
export function formatPeriodRange(option?: ArchivePeriodOption | null) {
  if (!option) return null;
  if (option.recurring_month && option.recurring_day) {
    const sample = new Date(Date.UTC(2024, option.recurring_month - 1, option.recurring_day));
    if (!Number.isNaN(sample.getTime()))
      return `Every ${sample.toLocaleDateString(undefined, { month: 'long', day: 'numeric', timeZone: 'UTC' })}`;
    return `Every ${option.recurring_month}/${option.recurring_day}`;
  }
  return `${option.date_from} → ${option.date_to}`;
}
export function uniquePeriodOptions(...groups: Array<ArchivePeriodOption[] | undefined>) {
  const seen = new Set<string>();
  return groups.flat().filter((option): option is ArchivePeriodOption => {
    if (!option || seen.has(option.slug)) return false;
    seen.add(option.slug);
    return true;
  });
}
export function periodKindLabel(kind?: string | null) {
  const normalized = normalizePeriodKind(kind) ?? 'latest';
  return PERIOD_KIND_TABS.find((tab) => tab.kind === normalized)?.label ?? 'Latest';
}
export function topicKindLabel(kind?: string | null) {
  const value = (kind || 'topic').toLowerCase();
  if (value.includes('series')) return 'Series';
  if (value.includes('category')) return 'Category';
  if (value.includes('person')) return 'Person';
  return 'Topic';
}
export function metadataChips(video: VideoInfo) {
  return [
    ...(video.people ?? []).map((person) => ({
      key: `person-${person.slug}`,
      label: person.display_name,
    })),
    ...(video.tags ?? []).map((tag) => ({ key: `tag-${tag.slug}`, label: tag.label })),
  ];
}
