let apiPromise: Promise<void> | null = null;

export function loadYouTubeApi(): Promise<void> {
  if (window.YT?.Player) return Promise.resolve();
  if (apiPromise) return apiPromise;

  apiPromise = new Promise<void>((resolve, reject) => {
    const existing = document.querySelector<HTMLScriptElement>('script[data-youtube-iframe-api]');
    const script = existing ?? document.createElement('script');
    const previousReady = window.onYouTubeIframeAPIReady;
    window.onYouTubeIframeAPIReady = () => {
      previousReady?.();
      resolve();
    };
    script.addEventListener(
      'error',
      () => {
        apiPromise = null;
        reject(new Error('YouTube player script failed to load'));
      },
      { once: true }
    );
    if (!existing) {
      script.src = 'https://www.youtube.com/iframe_api';
      script.dataset.youtubeIframeApi = 'true';
      document.body.appendChild(script);
    }
  });
  return apiPromise;
}

export function resetYouTubeApiForTests() {
  apiPromise = null;
}
