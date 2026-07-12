import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import AdminLayout from '../routes/admin/AdminLayout';

const auth = vi.hoisted(() => ({
  user: { id: 'user-1', email: 'user@example.com' } as { id: string; email: string } | null,
  loading: false,
  capabilities: [] as string[],
}));

vi.mock('../services/auth', () => ({ useAuth: () => auth }));
vi.mock('../services', () => ({ useAuth: () => auth }));

describe('AdminLayout access', () => {
  beforeEach(() => {
    auth.user = { id: 'user-1', email: 'user@example.com' };
    auth.loading = false;
    auth.capabilities = [];
  });

  it('renders a 403 state without loading admin children for non-admin users', () => {
    render(<AdminLayout />, { wrapper: MemoryRouter });
    expect(screen.getByText('Admin access required')).toBeInTheDocument();
    expect(screen.queryByText('Dashboard')).not.toBeInTheDocument();
  });

  it('renders the admin shell only with the admin capability', () => {
    auth.capabilities = ['admin:access'];
    render(<AdminLayout />, { wrapper: MemoryRouter });
    expect(screen.getByText('Dashboard')).toBeInTheDocument();
  });
});
