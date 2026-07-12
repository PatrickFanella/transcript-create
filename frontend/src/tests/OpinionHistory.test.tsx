import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import axe from 'axe-core';
import OpinionHistory from '../components/archive/OpinionHistory';

describe('OpinionHistory', () => {
  it('labels model output and provides direct citations with an accessible revision history', async () => {
    const { container } = render(
      <MemoryRouter>
        <OpinionHistory
          canCorrect={false}
          onCorrect={vi.fn()}
          onRetract={vi.fn()}
          items={[
            {
              id: 'opinion-1',
              subject_slug: 'housing',
              normalized_claim: 'Housing should be affordable',
              status: 'published',
              current_revision: 1,
              revisions: [
                {
                  revision: 1,
                  stance: 'support',
                  summary: 'Supports affordable housing.',
                  confidence: 0.95,
                  model_version: 'model-1',
                  prompt_version: 'prompt-1',
                  time_bucket: '2026-Q2',
                  model_generated: true,
                  status: 'published',
                  created_at: '2026-07-12T00:00:00Z',
                  evidence: [
                    {
                      video_id: 'video-1',
                      start_ms: 12000,
                      end_ms: 15000,
                      excerpt: 'Housing should be affordable.',
                    },
                  ],
                },
              ],
            },
          ]}
        />
      </MemoryRouter>
    );
    expect(screen.getByText('Model-generated')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /00:12/ })).toHaveAttribute('href', '/v/video-1?t=12');
    expect((await axe.run(container)).violations).toEqual([]);
  });
});
