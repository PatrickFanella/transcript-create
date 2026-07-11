export type Plan = 'free' | 'pro' | 'admin' | null | undefined;
export type ExportFormat = 'txt' | 'srt' | 'vtt' | 'json' | 'pdf';

export function canExportFormat(args: { plan: Plan; format: ExportFormat }): boolean {
  void args;
  return true;
}
