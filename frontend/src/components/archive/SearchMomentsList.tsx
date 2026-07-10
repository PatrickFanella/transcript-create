import type { SearchHit } from '../../types/api';
import { formatTimestamp } from '../../features/archive/format';
import HighlightedSnippet from '../HighlightedSnippet';
import MomentActionRow from './MomentActionRow';

type SearchMomentsListProps = {
  videoId: string;
  moments: SearchHit[];
  fallbackTitle: string;
  query: string;
  savedKeys: Set<string>;
  onSaveMoment: (videoId: string, moment: SearchHit, title: string) => void;
  onCopyTimestamp: (videoId: string, moment: SearchHit) => void;
  onCopyQuote: (videoId: string, moment: SearchHit, title: string) => void;
  onTrackResultClick: (videoId: string, moment: SearchHit) => void;
};

export default function SearchMomentsList({
  videoId,
  moments,
  fallbackTitle,
  query,
  savedKeys,
  onSaveMoment,
  onCopyTimestamp,
  onCopyQuote,
  onTrackResultClick,
}: SearchMomentsListProps) {
  return (
    <ol className="divide-y divide-border/70">
      {moments.map((moment, index) => {
        const key = `${videoId}:${moment.start_ms}:${moment.end_ms}`;

        return (
          <li key={moment.id} className="evidence-card group">
            <div className="grid gap-4 sm:grid-cols-[5rem_minmax(0,1fr)]">
              <div className="flex items-center gap-3 sm:block">
                <div className="font-mono text-[10px] text-subtle">
                  {String(index + 1).padStart(2, '0')}
                </div>
                <time className="mt-0 block font-mono text-xs font-semibold text-warning sm:mt-2">
                  {formatTimestamp(moment.start_ms)}
                </time>
                <div className="mt-0 font-mono text-[9px] text-subtle sm:mt-1">
                  {formatTimestamp(moment.end_ms)}
                </div>
              </div>
              <div className="min-w-0">
                <HighlightedSnippet
                  as="div"
                  className="archive-snippet"
                  snippet={moment.snippet}
                  highlights={moment.highlights}
                />
                <MomentActionRow
                  videoId={videoId}
                  moment={moment}
                  query={query}
                  saved={savedKeys.has(key)}
                  onOpenTimestamp={() => onTrackResultClick(videoId, moment)}
                  onCopyTimestamp={() => onCopyTimestamp(videoId, moment)}
                  onCopyQuote={() => onCopyQuote(videoId, moment, fallbackTitle)}
                  onSaveMoment={() => onSaveMoment(videoId, moment, fallbackTitle)}
                />
              </div>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
