import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import axe from 'axe-core';
import FavoritesPage from '../routes/FavoritesPage';

vi.mock('../services', () => ({
  useAuth: () => ({ user: null }),
  favorites: { list: () => [], toggle: vi.fn(), remove: vi.fn() },
  localSavedSearches: { list: () => [], add: vi.fn(), remove: vi.fn() },
  apiAddFavorite: vi.fn(),
  apiCreateSavedSearch: vi.fn(),
  apiDeleteFavorite: vi.fn(),
  apiDeleteSavedSearch: vi.fn(),
  apiListFavorites: vi.fn(),
  apiListSavedSearches: vi.fn(),
}));

describe('FavoritesPage accessibility', () => {
  it('keeps anonymous saved moments and searches accessible', async () => {
    const { container } = render(
      <MemoryRouter initialEntries={['/saved?q=rent']}>
        <FavoritesPage />
      </MemoryRouter>
    );
    expect(screen.getByRole('heading', { name: 'Saved moments and searches' })).toBeInTheDocument();
    expect(screen.getByDisplayValue('rent')).toBeInTheDocument();
    expect((await axe.run(container)).violations).toEqual([]);
  });
});
