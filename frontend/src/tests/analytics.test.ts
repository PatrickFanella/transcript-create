import { beforeEach, describe, expect, it, vi } from 'vitest';

describe('analytics unload transport', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}')));
  });

  it('uses a CSRF-capable keepalive request instead of sendBeacon', async () => {
    const beacon = vi.fn();
    Object.defineProperty(navigator, 'sendBeacon', { configurable: true, value: beacon });
    const { setCsrfToken } = await import('../services/api');
    const { track } = await import('../services/analytics');
    setCsrfToken('analytics-csrf-token');
    track({ type: 'search' });

    window.dispatchEvent(new Event('beforeunload'));

    await vi.waitFor(() => expect(fetch).toHaveBeenCalled());
    const request = (fetch as ReturnType<typeof vi.fn>).mock.calls[0][0] as Request;
    expect(request.url).toMatch(/events\/batch$/);
    expect(request.headers.get('X-CSRF-Token')).toBe('analytics-csrf-token');
    expect(beacon).not.toHaveBeenCalled();
  });
});
