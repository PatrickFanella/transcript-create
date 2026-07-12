import { Link } from 'react-router-dom';
import type { TopicTimelineResponse } from '../../types/api';
import { buildTimestampLink, formatTimestamp } from '../../features/archive/format';

export default function TopicTimeline({ data }: { data: TopicTimelineResponse }) {
  const max = Math.max(1, ...data.buckets.map((bucket) => bucket.mention_count));
  return (
    <section className="surface-card space-y-5" aria-labelledby="topic-timeline-title">
      <div>
        <h2 id="topic-timeline-title" className="section-title">
          Mentions over time
        </h2>
        <p className="mt-2 text-sm text-muted">
          Mention and episode counts backed by timestamped transcript evidence.
        </p>
      </div>
      <div
        className="flex h-40 items-end gap-2 border-b border-border px-2"
        role="img"
        aria-label={`Timeline of ${data.topic} mentions`}
      >
        {data.buckets.map((bucket) => {
          const height = Math.max(1, Math.ceil((bucket.mention_count / max) * 10));
          return (
            <div
              key={bucket.period}
              className="flex min-w-0 flex-1 flex-col items-center justify-end gap-1"
            >
              <span className="text-xs text-muted">{bucket.mention_count}</span>
              <div className={`topic-timeline-bar topic-timeline-bar-${height}`} />
              <span className="max-w-full truncate text-[10px] text-subtle">{bucket.period}</span>
            </div>
          );
        })}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <caption className="sr-only">Accessible topic timeline data</caption>
          <thead>
            <tr>
              <th scope="col">Period</th>
              <th scope="col">Mentions</th>
              <th scope="col">Episodes</th>
              <th scope="col">Evidence</th>
            </tr>
          </thead>
          <tbody>
            {data.buckets.map((bucket) => (
              <tr key={bucket.period} className="border-t border-border">
                <th scope="row" className="py-3 pr-3">
                  {bucket.label}
                </th>
                <td>{bucket.mention_count}</td>
                <td>{bucket.episode_count}</td>
                <td>
                  <div className="flex flex-wrap gap-2">
                    {bucket.evidence.map((moment, index) => (
                      <Link
                        key={`${moment.video.id}:${moment.start_ms}:${index}`}
                        className="action-link"
                        to={buildTimestampLink(moment.video.id, moment.start_ms)}
                      >
                        {formatTimestamp(moment.start_ms)}
                      </Link>
                    ))}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
