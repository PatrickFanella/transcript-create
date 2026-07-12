import { act, render } from '@testing-library/react';
import { createRef } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import PlaybackProgress from '../components/video/PlaybackProgress';
import type { YouTubePlayerHandle } from '../components/YouTubePlayer';

describe('PlaybackProgress', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('updates only the previous and next active transcript nodes', () => {
    let seconds = 0.5;
    const playerRef = createRef<YouTubePlayerHandle>();
    playerRef.current = { getCurrentTime: () => seconds } as YouTubePlayerHandle;
    const { container } = render(
      <div>
        <button data-transcript-sentence="true" data-start-ms="0" data-end-ms="1000">
          First
        </button>
        <button data-transcript-sentence="true" data-start-ms="1000" data-end-ms="2000">
          Second
        </button>
        <PlaybackProgress
          chapters={[]}
          onSelectChapter={vi.fn()}
          playerRef={playerRef}
          transcriptKey="video:2"
          autoFollow={false}
          onAutoScroll={vi.fn()}
        />
      </div>
    );
    const [first, second] = Array.from(
      container.querySelectorAll<HTMLElement>('[data-transcript-sentence="true"]')
    );

    act(() => vi.advanceTimersByTime(750));
    expect(first).toHaveClass('transcript-sentence-current');
    expect(second).not.toHaveClass('transcript-sentence-current');

    seconds = 1.5;
    act(() => vi.advanceTimersByTime(750));
    expect(first).not.toHaveClass('transcript-sentence-current');
    expect(second).toHaveClass('transcript-sentence-current');
  });
});
