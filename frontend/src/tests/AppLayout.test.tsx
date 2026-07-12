import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import axe from 'axe-core';
import AppLayout from '../routes/AppLayout';

vi.mock('../services', () => ({
  useAuth: () => ({
    user: null,
    loading: false,
    login: vi.fn(),
    loginTwitch: vi.fn(),
    logout: vi.fn(),
  }),
  useTheme: () => ({ theme: 'dark', toggleTheme: vi.fn() }),
}));

describe('AppLayout navigation', () => {
  it('includes Timeline in primary navigation', () => {
    const { container } = render(<AppLayout />, { wrapper: MemoryRouter });
    expect(screen.getByRole('link', { name: 'Timeline' })).toHaveAttribute('href', '/timeline');
    return axe.run(container).then((result) => expect(result.violations).toEqual([]));
  });

  it('only renders the mobile menu while open and restores focus on Escape', () => {
    render(<AppLayout />, { wrapper: MemoryRouter });
    const button = screen.getByRole('button', { name: 'Open menu' });
    expect(screen.queryByRole('navigation', { name: 'Mobile navigation' })).not.toBeInTheDocument();

    fireEvent.click(button);
    expect(screen.getByRole('navigation', { name: 'Mobile navigation' })).toBeInTheDocument();
    fireEvent.keyDown(document, { key: 'Escape' });

    expect(screen.queryByRole('navigation', { name: 'Mobile navigation' })).not.toBeInTheDocument();
    expect(button).toHaveFocus();
  });
});
