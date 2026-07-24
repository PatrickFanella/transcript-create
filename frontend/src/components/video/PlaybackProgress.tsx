import { useEffect, useRef, useState } from 'react';
import type { RefObject } from 'react';
import type { VideoChapter } from '../../types/api';
import type { YouTubePlayerHandle } from '../YouTubePlayer';
import EpisodeOutline from './EpisodeOutline';

type Props = {
  chapters: VideoChapter[];
  onSelectChapter: (chapter: VideoChapter) => void;
  playerRef: RefObject<YouTubePlayerHandle | null>;
  transcriptKey: string;
  autoFollow: boolean;
  onAutoScroll: (element: HTMLElement) => void;
};

export default function PlaybackProgress({
  chapters,
  onSelectChapter,
  playerRef,
  transcriptKey,
  autoFollow,
  onAutoScroll,
}: Props) {
  const [currentMs, setCurrentMs] = useState<number | null>(null);
  const currentElementRef = useRef<HTMLElement | null>(null);
  const lastScrollAtRef = useRef(0);

  useEffect(() => {
    const sentenceElements = Array.from(
      document.querySelectorAll<HTMLElement>('[data-transcript-sentence="true"]')
    );
    const starts = sentenceElements.map((element) => Number(element.dataset.startMs ?? 0));

    const interval = window.setInterval(() => {
      const seconds = playerRef.current?.getCurrentTime();
      if (seconds == null || !Number.isFinite(seconds)) return;
      const nextMs = Math.floor(seconds * 1000);
      setCurrentMs(nextMs);

      let low = 0;
      let high = starts.length - 1;
      let candidate = -1;
      while (low <= high) {
        const middle = (low + high) >> 1;
        if (starts[middle] <= nextMs) {
          candidate = middle;
          low = middle + 1;
        } else {
          high = middle - 1;
        }
      }
      const nextElement = candidate >= 0 ? sentenceElements[candidate] : null;
      const endMs = Number(nextElement?.dataset.endMs ?? 0);
      const activeElement = nextElement && nextMs < endMs ? nextElement : null;
      if (activeElement === currentElementRef.current) return;

      currentElementRef.current?.classList.remove('transcript-sentence-current');
      currentElementRef.current?.removeAttribute('data-current-sentence');
      currentElementRef.current = activeElement;
      activeElement?.classList.add('transcript-sentence-current');
      activeElement?.setAttribute('data-current-sentence', 'true');

      const now = Date.now();
      if (autoFollow && activeElement && now - lastScrollAtRef.current >= 1500) {
        onAutoScroll(activeElement);
        lastScrollAtRef.current = now;
      }
    }, 750);

    return () => {
      window.clearInterval(interval);
      currentElementRef.current?.classList.remove('transcript-sentence-current');
      currentElementRef.current?.removeAttribute('data-current-sentence');
      currentElementRef.current = null;
    };
  }, [autoFollow, onAutoScroll, playerRef, transcriptKey]);

  return <EpisodeOutline chapters={chapters} currentMs={currentMs} onSelect={onSelectChapter} />;
}
