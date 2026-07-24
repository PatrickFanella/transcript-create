import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import axe from 'axe-core';
import AppLayout from '../routes/AppLayout';

const auth = vi.hoisted(() => ({ user: null as { id: string; email: string } | null }));

vi.mock('../services', () => ({
  useAuth: () => ({
    user: auth.user,
    loading: false,
    login: vi.fn(),
    loginTwitch: vi.fn(),
    logout: vi.fn(),
  }),
  useTheme: () => ({ theme: 'dark', toggleTheme: vi.fn() }),
}));

describe('AppLayout navigation', () => {
  beforeEach(() => {
    auth.user = null;
  });
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

  it('exposes Account navigation only to authenticated users in desktop and mobile navigation', () => {
    auth.user = { id: 'user-1', email: 'person@example.com' };
    render(<AppLayout />, { wrapper: MemoryRouter });
    expect(screen.getByRole('link', { name: 'Account' })).toHaveAttribute('href', '/account');
    fireEvent.click(screen.getByRole('button', { name: 'Open menu' }));
    expect(
      within(screen.getByRole('navigation', { name: 'Mobile navigation' })).getByRole('link', {
        name: 'Account',
      })
    ).toHaveAttribute('href', '/account');
  });
});
