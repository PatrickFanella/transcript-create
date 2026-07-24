import { Link } from 'react-router-dom';
import type { OpinionHistoryItem } from '../../types/api';
import { buildTimestampLink, formatTimestamp } from '../../features/archive/format';

type Props = {
  items: OpinionHistoryItem[];
  canCorrect: boolean;
  onCorrect: (item: OpinionHistoryItem) => void;
  onRetract: (item: OpinionHistoryItem) => void;
};

export default function OpinionHistory({ items, canCorrect, onCorrect, onRetract }: Props) {
  return (
    <section className="surface-card space-y-4" aria-labelledby="opinion-history-title">
      <div>
        <h2 id="opinion-history-title" className="section-title">
          Opinion history
        </h2>
        <p className="mt-2 text-sm text-muted">
          Model-generated summaries with preserved revisions and direct citations.
        </p>
      </div>
      {items.length === 0 && (
        <p className="text-muted">No published opinion history is available.</p>
      )}
      {items.map((item) => {
        const current =
          item.revisions.find((revision) => revision.revision === item.current_revision) ??
          item.revisions[0];
        if (!current) return null;
        return (
          <article key={item.id} className="rounded-xl border border-border bg-surface-muted p-4">
            <div className="flex flex-wrap items-center gap-2">
              <span className="badge-warning">Model-generated</span>
              <span className="source-pill">{current.stance}</span>
              <span className="text-xs text-muted">
                {Math.round(current.confidence * 100)}% confidence
              </span>
            </div>
            <h3 className="mt-3 font-semibold text-ink">{item.normalized_claim}</h3>
            <p className="mt-2 text-sm text-muted">{current.summary}</p>
            <div className="mt-3 flex flex-wrap gap-2">
              {current.evidence.map((evidence) => (
                <Link
                  key={`${evidence.video_id}:${evidence.start_ms}`}
                  className="action-link text-sm"
                  to={buildTimestampLink(evidence.video_id, evidence.start_ms)}
                >
                  {formatTimestamp(evidence.start_ms)} — {evidence.excerpt}
                </Link>
              ))}
            </div>
            <details className="mt-3">
              <summary className="nav-link">Revision history ({item.revisions.length})</summary>
              <ol className="mt-2 space-y-2 text-sm text-muted">
                {item.revisions.map((revision) => (
                  <li key={revision.revision}>
                    Revision {revision.revision}: {revision.status} · {revision.model_version}/
                    {revision.prompt_version} · {revision.time_bucket}
                    {revision.correction_reason ? ` — ${revision.correction_reason}` : ''}
                  </li>
                ))}
              </ol>
            </details>
            {canCorrect && (
              <div className="mt-3 flex gap-2">
                <button className="btn-secondary" type="button" onClick={() => onCorrect(item)}>
                  Correct
                </button>
                <button className="btn-ghost" type="button" onClick={() => onRetract(item)}>
                  Retract
                </button>
              </div>
            )}
          </article>
        );
      })}
    </section>
  );
}
