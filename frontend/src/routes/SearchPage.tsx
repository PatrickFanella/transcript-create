import { useEffect, useMemo, useState } from 'react';
import type { FormEvent } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { api, apiAddFavorite, favorites, track, useAuth } from '../services';
import type {
  ArchiveSearchFilters,
  GroupedSearchResponse,
  SearchHit,
  VideoInfo,
} from '../types/api';
import {
  buildTimestampLink,
  formatDate,
  formatDuration,
  formatNumber,
} from '../features/archive/format';
import { buildCurrentFilters, readFilters, serializeFilters } from '../features/search/filters';
import {
  buildPlayMatchesLink,
  buildQuoteText,
  plainTextFromSnippet,
} from '../features/search/moments';
import { groupHitsByVideo } from '../features/searchTranscript/matches';
import { SearchFiltersPanel, SearchMomentsList } from '../components/archive';

type SearchMode = 'grouped' | 'flat' | null;

function copyText(text: string) {
  void navigator.clipboard?.writeText(text);
}

function ResultHeader({
  video,
  title,
  count,
  query,
  firstMoment,
}: {
  video?: VideoInfo;
  title: string;
  count: number;
  query: string;
  firstMoment?: SearchHit;
}) {
  return (
    <header className="search-result-head">
      <Link
        to={video ? `/v/${video.id}` : '#'}
        className="relative hidden aspect-video overflow-hidden rounded-lg border border-border bg-canvas sm:block lg:aspect-[4/3]"
      >
        {video?.youtube_id ? (
          <img
            src={`https://i.ytimg.com/vi/${video.youtube_id}/mqdefault.jpg`}
            alt=""
            className="h-full w-full object-cover opacity-80 transition-opacity hover:opacity-100"
            loading="lazy"
          />
        ) : null}
      </Link>
      <div className="min-w-0 space-y-2">
        <div className="archive-eyebrow">VOD dossier</div>
        <Link
          to={video ? `/v/${video.id}` : '#'}
          className="block text-xl font-semibold leading-6 tracking-[-0.035em] text-ink transition-colors hover:text-accent sm:text-2xl"
        >
          {title}
        </Link>
        <div className="flex flex-wrap gap-2">
          {video?.channel_name && <span className="source-pill">{video.channel_name}</span>}
          {video?.uploaded_at && (
            <span className="timestamp-pill">{formatDate(video.uploaded_at)}</span>
          )}
          {video?.duration_seconds ? (
            <span className="source-pill">{formatDuration(video.duration_seconds)}</span>
          ) : null}
        </div>
      </div>
      <div className="flex items-start justify-between gap-3 lg:block lg:text-right">
        <div>
          <div className="font-mono text-3xl font-semibold tracking-[-0.05em] text-ink">
            {formatNumber(count)}
          </div>
          <div className="meta-label mt-1">{count === 1 ? 'match' : 'matches'}</div>
        </div>
        {video && query && firstMoment && (
          <Link
            to={buildPlayMatchesLink(video.id, firstMoment, query)}
            className="action-link mt-0 inline-block text-xs lg:mt-4"
          >
            Play all matches
          </Link>
        )}
      </div>
    </header>
  );
}

export default function SearchPage() {
  const [params, setParams] = useSearchParams();
  const filters = useMemo(() => readFilters(params), [params]);
  const [q, setQ] = useState(filters.q);
  const [dateFrom, setDateFrom] = useState(filters.date_from ?? '');
  const [dateTo, setDateTo] = useState(filters.date_to ?? '');
  const [suggestedSearches, setSuggestedSearches] = useState<Array<{ term: string }>>([]);
  const [loading, setLoading] = useState(false);
  const [mode, setMode] = useState<SearchMode>(null);
  const [grouped, setGrouped] = useState<GroupedSearchResponse | null>(null);
  const [flatHits, setFlatHits] = useState<SearchHit[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [savedKeys, setSavedKeys] = useState<Set<string>>(new Set());
  const { user } = useAuth();
  const shouldFetch = Boolean(filters.q.trim());
  const canSubmitSearch = Boolean(q.trim());

  useEffect(() => {
    setQ(filters.q);
    setDateFrom(filters.date_from ?? '');
    setDateTo(filters.date_to ?? '');
  }, [filters.q, filters.date_from, filters.date_to]);

  useEffect(() => {
    let cancelled = false;
    api
      .getExploreIntelligence()
      .then((response) => {
        if (!cancelled) setSuggestedSearches(response.suggested_searches ?? []);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          console.error('Failed to load suggested searches', err);
          setSuggestedSearches([]);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const query = filters.q.trim();
    if (!shouldFetch) {
      setGrouped(null);
      setFlatHits([]);
      setMode(null);
      setLoading(false);
      setError(null);
      return;
    }

    const activeFilters: ArchiveSearchFilters = {
      source: undefined,
      category: undefined,
      date_from: filters.date_from,
      date_to: filters.date_to,
      min_duration: undefined,
      max_duration: undefined,
      sort_by: undefined,
      video_id: filters.video_id,
      limit: filters.limit,
      offset: filters.offset,
    };

    setLoading(true);
    setError(null);
    track({ type: 'search', payload: { q: query, ...activeFilters } });

    api
      .searchGrouped(query, activeFilters)
      .then((response) => {
        setGrouped(response);
        setFlatHits([]);
        setMode('grouped');
      })
      .catch(() =>
        api
          .search(query, activeFilters)
          .then((response) => {
            setGrouped(null);
            setFlatHits(response.hits ?? []);
            setMode('flat');
          })
          .catch((flatError: unknown) => {
            console.error(flatError);
            setError('Search failed. Try the query again or broaden the date range.');
          })
      )
      .finally(() => setLoading(false));
  }, [
    filters.date_from,
    filters.date_to,
    filters.limit,
    filters.offset,
    filters.q,
    filters.video_id,
    shouldFetch,
  ]);

  function submitFilters(event: FormEvent) {
    event.preventDefault();
    setParams(
      serializeFilters({
        q,
        date_from: dateFrom || undefined,
        date_to: dateTo || undefined,
        video_id: filters.video_id,
        limit: filters.limit,
        offset: filters.offset,
      })
    );
  }

  function resetFilters() {
    setQ('');
    setDateFrom('');
    setDateTo('');
    setParams(new URLSearchParams());
  }

  const groupedGroups = grouped?.groups ?? [];
  const totalMoments = grouped?.total_moments ?? flatHits.length;
  const fallbackGroups = useMemo(() => groupHitsByVideo(flatHits), [flatHits]);
  const totalVideos = grouped?.total_videos ?? fallbackGroups.length;

  async function saveMoment(videoId: string, moment: SearchHit, title: string) {
    const key = `${videoId}:${moment.start_ms}:${moment.end_ms}`;
    const text = plainTextFromSnippet(moment.snippet, moment.highlights);
    try {
      if (user)
        await apiAddFavorite({
          video_id: videoId,
          start_ms: moment.start_ms,
          end_ms: moment.end_ms,
          text,
        });
      else
        favorites.toggle({
          videoId,
          segIndex: moment.id,
          startMs: moment.start_ms,
          endMs: moment.end_ms,
          text,
        });
      setSavedKeys((current) => new Set([...current, key]));
      track({ type: 'favorite_add', payload: { videoId, start_ms: moment.start_ms, title } });
    } catch (err) {
      console.error('Failed to save moment', err);
      setError('Could not save this moment. Sign in again or try later.');
    }
  }

  function copyMomentTimestamp(videoId: string, moment: SearchHit) {
    copyText(`${window.location.origin}${buildTimestampLink(videoId, moment.start_ms, moment.id)}`);
  }

  function copyMomentQuote(videoId: string, moment: SearchHit, title: string) {
    copyText(buildQuoteText(videoId, moment, title));
  }

  return (
    <div className="space-y-5 lg:space-y-7">
      <section className="archive-masthead p-5 sm:p-8 lg:p-10">
        <div className="relative z-10 mx-auto max-w-5xl space-y-7">
          <div className="text-center">
            <div className="archive-eyebrow">Transcript search</div>
            <h1 className="mt-4 text-4xl font-semibold tracking-[-0.055em] text-ink sm:text-6xl">
              Search the record.
            </h1>
            <p className="mx-auto mt-3 max-w-2xl text-base leading-7 text-muted sm:text-lg">
              Enter a topic or exact phrase. Results are grouped by broadcast and open directly at
              the matching transcript moment.
            </p>
          </div>
          <SearchFiltersPanel
            q={q}
            dateFrom={dateFrom}
            dateTo={dateTo}
            loading={loading}
            canSubmitSearch={canSubmitSearch}
            onQChange={setQ}
            onDateFromChange={setDateFrom}
            onDateToChange={setDateTo}
            onSubmit={submitFilters}
            onReset={resetFilters}
          />
        </div>
      </section>

      {error && (
        <div className="alert-warning" role="alert">
          {error}
        </div>
      )}

      {shouldFetch ? (
        <div className="search-workspace">
          <main className="min-w-0 space-y-4">
            <section className="flex flex-col gap-3 border-b border-border pb-4 sm:flex-row sm:items-end sm:justify-between">
              <div>
                <div className="archive-eyebrow">Search report</div>
                <h2 className="mt-3 text-3xl font-semibold tracking-[-0.045em] text-ink">
                  “{filters.q}”
                </h2>
              </div>
              <div className="flex flex-wrap items-center gap-3 text-sm text-muted">
                <span>
                  {loading
                    ? 'Scanning transcripts…'
                    : `${formatNumber(totalMoments)} moments in ${formatNumber(totalVideos)} VODs`}
                </span>
                {!loading && (
                  <span className="h-1.5 w-1.5 rounded-full bg-accent" aria-hidden="true" />
                )}
              </div>
            </section>

            {loading && (
              <div
                className="search-result-card animate-pulse p-6"
                role="status"
                aria-live="polite"
              >
                <div className="h-4 w-24 rounded bg-surface-muted" />
                <div className="mt-4 h-7 w-2/3 rounded bg-surface-muted" />
                <div className="mt-8 space-y-3">
                  <div className="h-20 rounded bg-surface-muted" />
                  <div className="h-20 rounded bg-surface-muted" />
                </div>
                <span className="sr-only">Searching the archive…</span>
              </div>
            )}

            {!loading &&
              mode === 'grouped' &&
              groupedGroups.map((group) => {
                const title = group.video.title || `VOD ${group.video.id.slice(0, 8)}…`;
                return (
                  <article key={group.video.id} className="search-result-card">
                    <ResultHeader
                      video={group.video}
                      title={title}
                      count={group.moments.length}
                      query={filters.q}
                      firstMoment={group.moments[0] as SearchHit | undefined}
                    />
                    <SearchMomentsList
                      videoId={group.video.id}
                      moments={group.moments as SearchHit[]}
                      fallbackTitle={title}
                      query={filters.q}
                      savedKeys={savedKeys}
                      onSaveMoment={saveMoment}
                      onCopyTimestamp={copyMomentTimestamp}
                      onCopyQuote={copyMomentQuote}
                      onTrackResultClick={(videoId, moment) =>
                        track({
                          type: 'result_click',
                          payload: { videoId, start_ms: moment.start_ms, id: moment.id },
                        })
                      }
                    />
                  </article>
                );
              })}

            {!loading &&
              mode === 'flat' &&
              fallbackGroups.map(([videoId, hits]) => {
                const title = `VOD ${videoId.slice(0, 8)}…`;
                return (
                  <article key={videoId} className="search-result-card">
                    <ResultHeader title={title} count={hits.length} query={filters.q} />
                    <SearchMomentsList
                      videoId={videoId}
                      moments={hits}
                      fallbackTitle={title}
                      query={filters.q}
                      savedKeys={savedKeys}
                      onSaveMoment={saveMoment}
                      onCopyTimestamp={copyMomentTimestamp}
                      onCopyQuote={copyMomentQuote}
                      onTrackResultClick={(resultVideoId, moment) =>
                        track({
                          type: 'result_click',
                          payload: {
                            videoId: resultVideoId,
                            start_ms: moment.start_ms,
                            id: moment.id,
                          },
                        })
                      }
                    />
                  </article>
                );
              })}

            {!loading &&
              ((mode === 'grouped' && groupedGroups.length === 0) ||
                (mode === 'flat' && fallbackGroups.length === 0)) && (
                <div className="archive-section py-14 text-center">
                  <div className="font-mono text-4xl text-subtle">∅</div>
                  <h3 className="mt-4 text-xl font-semibold text-ink">No transcript matches</h3>
                  <p className="mx-auto mt-2 max-w-md text-sm leading-6 text-muted">
                    Try fewer words, remove the date range, or open one of the suggested searches.
                  </p>
                </div>
              )}
          </main>

          <aside className="space-y-4 xl:sticky xl:top-24">
            <section className="archive-section space-y-4">
              <div className="archive-rule-title">Research tools</div>
              <Link
                to={`/topics/${encodeURIComponent(filters.q)}`}
                className="block rounded-lg border border-border bg-surface-muted p-3 text-sm text-ink transition-colors hover:border-accent/50"
              >
                <strong className="block">Mention map</strong>
                <span className="mt-1 block text-xs leading-5 text-muted">
                  See this topic across time and broadcasts.
                </span>
              </Link>
              <Link
                to={`/saved?${buildCurrentFilters(q, undefined, dateFrom, dateTo, '', '', '', 'relevance', filters).toString()}`}
                className="block rounded-lg border border-border bg-surface-muted p-3 text-sm text-ink transition-colors hover:border-accent/50"
              >
                <strong className="block">Save this query</strong>
                <span className="mt-1 block text-xs leading-5 text-muted">
                  Keep these filters for your account.
                </span>
              </Link>
            </section>

            {suggestedSearches.length > 0 && (
              <section className="archive-section space-y-4">
                <div className="archive-rule-title">Related starts</div>
                <div className="flex flex-wrap gap-2">
                  {suggestedSearches.slice(0, 10).map((item) => (
                    <Link
                      key={item.term}
                      to={`/search?q=${encodeURIComponent(item.term)}`}
                      className="source-pill hover:border-accent/50 hover:text-ink"
                    >
                      {item.term}
                    </Link>
                  ))}
                </div>
              </section>
            )}
          </aside>
        </div>
      ) : (
        <section className="grid gap-5 lg:grid-cols-[minmax(0,1.1fr)_minmax(20rem,0.9fr)]">
          <div className="archive-section py-10 sm:py-14">
            <div className="max-w-xl">
              <div className="archive-eyebrow">Start with a question</div>
              <h2 className="mt-4 text-3xl font-semibold tracking-[-0.045em] text-ink">
                What are you trying to find?
              </h2>
              <p className="mt-3 text-base leading-7 text-muted">
                Broad subjects reveal a topic history. Exact phrases work best when you remember a
                particular line.
              </p>
            </div>
          </div>
          <div className="archive-section">
            <div className="archive-rule-title">Suggested searches</div>
            <div className="mt-5 grid grid-cols-2 gap-2">
              {suggestedSearches.slice(0, 8).map((item, index) => (
                <Link
                  key={item.term}
                  to={`/search?q=${encodeURIComponent(item.term)}`}
                  className="rounded-lg border border-border bg-surface-muted p-3 transition-colors hover:border-accent/50"
                >
                  <span className="font-mono text-[9px] text-subtle">
                    {String(index + 1).padStart(2, '0')}
                  </span>
                  <span className="mt-2 block text-sm font-semibold text-ink">{item.term}</span>
                </Link>
              ))}
            </div>
          </div>
        </section>
      )}
    </div>
  );
}
