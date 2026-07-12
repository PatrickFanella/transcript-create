import { beforeEach, describe, expect, it, vi } from 'vitest';
import { playbackQueue } from '../services/playbackQueue';

describe('playbackQueue', () => {
  const item = {
    video_id: 'video-1',
    video_title: 'Episode',
    start_ms: 12000,
    end_ms: 18000,
    snippet: 'A mention',
    source: 'whisper',
    deep_link: '/v/video-1?t=12',
  };

  beforeEach(() => vi.clearAllMocks());

  it('persists, removes, and clears portable mention items', () => {
    vi.mocked(localStorage.getItem).mockReturnValueOnce(null);
    expect(playbackQueue.list()).toEqual([]);

    playbackQueue.replace([item]);
    expect(localStorage.setItem).toHaveBeenLastCalledWith(
      'hasanara.playback-queue.v1',
      JSON.stringify([item])
    );

    vi.mocked(localStorage.getItem).mockReturnValueOnce(JSON.stringify([item]));
    expect(playbackQueue.remove(0)).toEqual([]);
    expect(playbackQueue.clear()).toEqual([]);
  });
});
