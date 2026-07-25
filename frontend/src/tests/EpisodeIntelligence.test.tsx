import axeCore from 'axe-core';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import EpisodeIntelligence from '../components/video/EpisodeIntelligence';
import { api } from '../services';

describe('EpisodeIntelligence', () => {
  it('shows explainable related episodes and timestamped quoted moments', async () => {
    vi.spyOn(api, 'getRelatedEpisodes').mockResolvedValue({
      items: [
        {
          video: { id: 'related-1', youtube_id: 'abcdefghijk', title: 'Related VOD' },
          score: 2,
          reasons: ['Shared tag: Politics'],
        },
      ],
    });
    vi.spyOn(api, 'getQuotedMoments').mockResolvedValue({
      video_id: 'video-1',
      items: [{ start_ms: 12000, end_ms: 15000, snippet: 'Quoted text', quote_count: 3 }],
    });
    render(
      <MemoryRouter>
        <EpisodeIntelligence videoId="video-1" />
      </MemoryRouter>
    );
    expect(await screen.findByRole('link', { name: 'Related VOD' })).toHaveAttribute(
      'href',
      '/v/related-1'
    );
    expect(screen.getByText('Shared tag: Politics')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /quoted 3 times/ })).toHaveAttribute(
      'href',
      '/v/video-1?t=12'
    );
  });

  it('exposes empty and unavailable states without accessibility violations', async () => {
    vi.spyOn(api, 'getRelatedEpisodes').mockResolvedValue({ items: [] });
    vi.spyOn(api, 'getQuotedMoments').mockResolvedValue({ video_id: 'video-1', items: [] });
    const { container, rerender } = render(
      <MemoryRouter>
        <EpisodeIntelligence videoId="video-1" />
      </MemoryRouter>
    );
    expect(await screen.findByText('No explainable related episodes yet.')).toBeInTheDocument();
    expect(screen.getByText('No quoted moments have accumulated yet.')).toBeInTheDocument();

    vi.spyOn(api, 'getRelatedEpisodes').mockRejectedValue(new Error('degraded'));
    vi.spyOn(api, 'getQuotedMoments').mockRejectedValue(new Error('degraded'));
    rerender(
      <MemoryRouter>
        <EpisodeIntelligence videoId="video-2" />
      </MemoryRouter>
    );
    expect(await screen.findByRole('alert')).toHaveTextContent('temporarily unavailable');
    await expect(axeCore.run(container).then((result) => result.violations)).resolves.toEqual([]);
  });
});
