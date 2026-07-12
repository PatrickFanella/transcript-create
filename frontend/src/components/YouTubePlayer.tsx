import { useCallback, useEffect, useImperativeHandle, useRef, useState, forwardRef } from 'react';
import { loadYouTubeApi } from '../services/youtubeApi';

type YouTubePlayer = {
  destroy?: () => void;
  seekTo?: (seconds: number, allowSeekAhead: boolean) => void;
  playVideo?: () => void;
  pauseVideo?: () => void;
  getPlayerState?: () => number;
  getCurrentTime?: () => number;
};

type YouTubePlayerConstructor = {
  new (
    element: HTMLElement,
    config: {
      height: string;
      width: string;
      videoId: string;
      playerVars: { start: number; autoplay: number };
      events: { onReady: () => void };
    }
  ): YouTubePlayer;
};

declare global {
  interface Window {
    YT?: {
      Player?: YouTubePlayerConstructor;
    };
    onYouTubeIframeAPIReady?: () => void;
  }
}

export type YouTubePlayerHandle = {
  seekTo: (seconds: number, options?: { play?: boolean }) => void;
  play: () => void;
  pause: () => void;
  togglePlay: () => void;
  getCurrentTime: () => number | null;
};

type Props = { videoId: string; start?: number; title?: string };

const YOUTUBE_PLAYING_STATE = 1;

export default forwardRef<YouTubePlayerHandle, Props>(function YouTubePlayer(
  { videoId, start = 0, title },
  ref
) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const playerRef = useRef<YouTubePlayer | null>(null);
  const startRef = useRef(start);
  const previousStartRef = useRef(start);
  startRef.current = start;
  const pendingSeekRef = useRef<{ seconds: number; play: boolean } | null>(null);
  const [ready, setReady] = useState(false);
  const [scriptError, setScriptError] = useState<string | null>(null);

  const seek = useCallback(
    (seconds: number, play = false) => {
      pendingSeekRef.current = { seconds, play };
      if (!ready || !playerRef.current) return;
      try {
        playerRef.current.seekTo?.(seconds, true);
        const isPlaying = playerRef.current.getPlayerState?.() === YOUTUBE_PLAYING_STATE;
        if (play && !isPlaying) playerRef.current.playVideo?.();
        pendingSeekRef.current = null;
      } catch {
        // Suppress errors during seek; keep pending seek for the next ready transition
      }
    },
    [ready]
  );

  useEffect(() => {
    let active = true;
    setReady(false);
    setScriptError(null);
    const initialStart = startRef.current;
    pendingSeekRef.current = initialStart ? { seconds: initialStart, play: false } : null;
    void loadYouTubeApi()
      .then(() => {
        if (!active || !containerRef.current || !window.YT?.Player) return;
        playerRef.current = new window.YT.Player(containerRef.current, {
          height: '100%',
          width: '100%',
          videoId,
          playerVars: { start: initialStart, autoplay: 0 },
          events: {
            onReady: () => {
              if (active) setReady(true);
            },
          },
        });
      })
      .catch((error: unknown) => {
        if (active) setScriptError(error instanceof Error ? error.message : 'Player unavailable');
      });
    return () => {
      active = false;
      try {
        playerRef.current?.destroy?.();
      } catch {
        // Suppress errors on cleanup
      }
      playerRef.current = null;
    };
  }, [videoId]);

  useEffect(() => {
    const changed = previousStartRef.current !== start;
    previousStartRef.current = start;
    pendingSeekRef.current = start || changed ? { seconds: start, play: false } : null;
    if (ready && (start || changed)) seek(start, false);
  }, [ready, seek, start, videoId]);

  useImperativeHandle(ref, () => ({
    seekTo(seconds: number, options?: { play?: boolean }) {
      seek(seconds, options?.play ?? false);
    },
    play() {
      try {
        playerRef.current?.playVideo?.();
      } catch {
        // Suppress player API errors
      }
    },
    pause() {
      try {
        playerRef.current?.pauseVideo?.();
      } catch {
        // Suppress player API errors
      }
    },
    togglePlay() {
      try {
        if (playerRef.current?.getPlayerState?.() === YOUTUBE_PLAYING_STATE) {
          playerRef.current.pauseVideo?.();
        } else {
          playerRef.current?.playVideo?.();
        }
      } catch {
        // Suppress player API errors
      }
    },
    getCurrentTime() {
      try {
        return playerRef.current?.getCurrentTime?.() ?? null;
      } catch {
        return null;
      }
    },
  }));

  return (
    <div className="relative aspect-video w-full overflow-hidden bg-black">
      <div ref={containerRef} title={title ?? 'YouTube player'} className="h-full w-full" />
      {!ready && !scriptError && (
        <div
          className="absolute inset-0 grid place-items-center bg-black/70 text-white"
          role="status"
        >
          Loading player…
        </div>
      )}
      {scriptError && (
        <div
          className="absolute inset-0 grid place-items-center bg-black/80 p-6 text-white"
          role="alert"
        >
          {scriptError}
        </div>
      )}
    </div>
  );
});
