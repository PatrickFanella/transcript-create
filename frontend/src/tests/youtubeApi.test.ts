import { afterEach, describe, expect, it, vi } from 'vitest';
import { loadYouTubeApi, resetYouTubeApiForTests } from '../services/youtubeApi';

describe('YouTube API loader', () => {
  afterEach(() => {
    document.querySelectorAll('script[data-youtube-iframe-api]').forEach((node) => node.remove());
    delete window.YT;
    delete window.onYouTubeIframeAPIReady;
    resetYouTubeApiForTests();
    vi.restoreAllMocks();
  });

  it('shares one script and promise across callers', async () => {
    let script: HTMLScriptElement | null = null;
    const append = vi.spyOn(document.body, 'appendChild').mockImplementation((node) => {
      script = node as HTMLScriptElement;
      return node;
    });
    const first = loadYouTubeApi();
    const second = loadYouTubeApi();
    expect(first).toBe(second);
    expect(append).toHaveBeenCalledTimes(1);
    expect(script).not.toBeNull();

    window.onYouTubeIframeAPIReady?.();
    await expect(first).resolves.toBeUndefined();
  });

  it('reports script failures and permits a later retry', async () => {
    const scripts: HTMLScriptElement[] = [];
    const append = vi.spyOn(document.body, 'appendChild').mockImplementation((node) => {
      scripts.push(node as HTMLScriptElement);
      return node;
    });
    const first = loadYouTubeApi();
    scripts[0].dispatchEvent(new Event('error'));
    await expect(first).rejects.toThrow('failed to load');

    const retry = loadYouTubeApi();
    expect(append).toHaveBeenCalledTimes(2);
    scripts[1].dispatchEvent(new Event('error'));
    await expect(retry).rejects.toThrow('failed to load');
  });
});
