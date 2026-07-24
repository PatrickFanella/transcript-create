import { Link } from 'react-router-dom';
import type { RecentVideo } from '../../types/api';
import { formatDate, formatDuration } from '../../features/archive/format';
import VideoMetadataChips from './VideoMetadataChips';

type VideoCardProps = {
  video: RecentVideo;
};

export default function VideoCard({ video }: VideoCardProps) {
  const metadata = [
    ...(video.people ?? []).map((person) => ({
      key: `person-${person.slug}`,
      label: person.display_name,
    })),
    ...(video.tags ?? []).map((tag) => ({ key: `tag-${tag.slug}`, label: tag.label })),
  ];

  return (
    <Link
      to={`/v/${video.id}`}
      className="group overflow-hidden rounded-xl border border-border bg-surface-muted/55 transition-[transform,border-color,box-shadow] hover:-translate-y-0.5 hover:border-accent/65 hover:shadow-[0_18px_40px_rgba(0,0,0,0.28)]"
    >
      <div className="relative aspect-video overflow-hidden border-b border-border bg-canvas">
        <img
          src={`https://i.ytimg.com/vi/${video.youtube_id}/hqdefault.jpg`}
          alt=""
          className="h-full w-full object-cover opacity-85 transition duration-500 group-hover:scale-[1.025] group-hover:opacity-100"
          loading="lazy"
          width="480"
          height="360"
        />
        <div className="absolute inset-0 bg-gradient-to-t from-black/70 via-transparent to-transparent" />
        <span className="timestamp-pill absolute bottom-3 left-3">
          {formatDuration(video.duration_seconds)}
        </span>
        <span
          className={`absolute right-3 top-3 h-2.5 w-2.5 rounded-full ring-4 ring-black/35 ${video.has_whisper_transcript ? 'bg-accent' : 'bg-warning'}`}
          aria-hidden="true"
        />
      </div>

      <div className="p-4">
        <div className="line-clamp-2 min-h-[3rem] text-lg font-semibold leading-6 tracking-[-0.03em] text-ink transition-colors group-hover:text-accent">
          {video.title || 'Untitled VOD'}
        </div>
        <div className="mt-2 flex items-center justify-between gap-3 text-xs text-muted">
          <span className="truncate">{video.channel_name || 'Unknown channel'}</span>
          <span className="shrink-0 font-mono text-[10px] text-subtle">
            {formatDate(video.uploaded_at ?? null)}
          </span>
        </div>
        {metadata.length > 0 && (
          <div className="mt-4 border-t border-border/70 pt-3 text-xs">
            <VideoMetadataChips label="VOD metadata" items={metadata} limit={3} />
          </div>
        )}
      </div>
    </Link>
  );
}
