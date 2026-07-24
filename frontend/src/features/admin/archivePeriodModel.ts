import type { ArchiveNamedPeriodAdminResponse } from '../../types/api';

export type PeriodKind =
  | 'week'
  | 'month'
  | 'event'
  | 'date'
  | 'holiday'
  | 'anniversary'
  | 'leadup'
  | 'fallout';
export type PeriodStatus = 'published' | 'hidden';
export type PeriodFormState = {
  label: string;
  slug: string;
  kind: string;
  date_from: string;
  date_to: string;
  description: string;
  status: PeriodStatus;
  sort_order: string;
  recurring_month: string;
  recurring_day: string;
};

export const periodKinds: Array<{ value: 'all' | PeriodKind; label: string }> = [
  { value: 'all', label: 'All kinds' },
  { value: 'week', label: 'Week' },
  { value: 'month', label: 'Month' },
  { value: 'event', label: 'Event' },
  { value: 'date', label: 'Date' },
  { value: 'holiday', label: 'Holiday' },
  { value: 'anniversary', label: 'Anniversary' },
  { value: 'leadup', label: 'Leadup' },
  { value: 'fallout', label: 'Fallout' },
];
export const statusOptions: Array<{ value: 'all' | PeriodStatus; label: string }> = [
  { value: 'all', label: 'All statuses' },
  { value: 'published', label: 'Published' },
  { value: 'hidden', label: 'Hidden' },
];
export const emptyPeriodForm: PeriodFormState = {
  label: '',
  slug: '',
  kind: 'event',
  date_from: '',
  date_to: '',
  description: '',
  status: 'published',
  sort_order: '',
  recurring_month: '',
  recurring_day: '',
};

export function formatPeriodDuration(seconds?: number | null) {
  if (!seconds && seconds !== 0) return '—';
  const total = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (hours > 0) return `${hours}h ${minutes}m ${secs}s`;
  if (minutes > 0) return `${minutes}m ${secs}s`;
  return `${secs}s`;
}
export function formatPeriodDateTime(value?: string | null) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}
export function formatPeriodDateRange(
  row: Pick<
    ArchiveNamedPeriodAdminResponse,
    'date_from' | 'date_to' | 'recurring_month' | 'recurring_day'
  >
) {
  if (row.recurring_month && row.recurring_day) {
    const sample = new Date(Date.UTC(2024, row.recurring_month - 1, row.recurring_day));
    if (!Number.isNaN(sample.getTime()))
      return `Every ${sample.toLocaleDateString(undefined, { month: 'long', day: 'numeric', timeZone: 'UTC' })}`;
    return `Every ${row.recurring_month}/${row.recurring_day}`;
  }
  if (row.date_from && row.date_to) return `${row.date_from} → ${row.date_to}`;
  return row.date_from || row.date_to || '—';
}
