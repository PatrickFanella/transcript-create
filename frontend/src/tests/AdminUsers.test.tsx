import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import AdminUsers from '../routes/admin/AdminUsers';
import { AuthProvider } from '../services/auth';
import { setCsrfToken } from '../services/api';

const admin = { id: 'admin-1', email: 'admin@example.com', name: 'Admin' };
const target = {
  id: 'target-1',
  email: 'target@example.com',
  name: 'Target User',
  role: 'user',
  created_at: '2026-01-01T00:00:00Z',
};

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function renderUsers() {
  return render(
    <MemoryRouter>
      <AuthProvider>
        <AdminUsers />
      </AuthProvider>
    </MemoryRouter>
  );
}

describe('AdminUsers role controls', () => {
  beforeEach(() => setCsrfToken(null));
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('sends a CSRF-protected PUT role change and renders Moderator only after server success', async () => {
    const fetchMock = vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (path.endsWith('/auth/me'))
        return json({ user: admin, role: 'admin', capabilities: ['admin:access'] });
      if (path.endsWith('/auth/csrf')) return json({ csrf_token: 'csrf-token' });
      if (path.endsWith('/admin/users') && request.method === 'GET')
        return json({ items: [target] });
      if (path.endsWith('/admin/users/target-1/role')) {
        expect(request.method).toBe('PUT');
        expect(request.headers.get('X-CSRF-Token')).toBe('csrf-token');
        expect(await request.clone().json()).toEqual({ role: 'moderator' });
        return json({ user_id: target.id, role: 'moderator' });
      }
      return json({ error: 'unexpected' }, 500);
    });
    vi.stubGlobal('fetch', fetchMock);
    const events = userEvent.setup();
    renderUsers();
    const select = await screen.findByLabelText('Role for Target User');
    await events.selectOptions(select, 'moderator');
    await waitFor(() => expect(select).toHaveValue('moderator'));
    expect(screen.getByRole('option', { name: 'Moderator' })).toBeInTheDocument();
  });

  it.each([
    [409, { error: 'final_admin' }, 'The final administrator cannot be demoted.'],
    [403, { error: 'forbidden' }, 'Your admin access changed; refresh before trying again.'],
    [422, { error: 'validation_error' }, 'Choose User, Moderator, or Admin.'],
    [503, {}, 'The role service is temporarily unavailable. Try again.'],
  ])(
    'keeps the server role on %s errors and surfaces actionable feedback',
    async (status, body, message) => {
      const fetchMock = vi.fn((request: Request) =>
        Promise.resolve(
          (() => {
            const path = new URL(request.url).pathname;
            if (path.endsWith('/auth/me'))
              return json({ user: admin, role: 'admin', capabilities: ['admin:access'] });
            if (path.endsWith('/auth/csrf')) return json({ csrf_token: 'csrf-token' });
            if (path.endsWith('/admin/users') && request.method === 'GET')
              return json({ items: [target] });
            if (path.endsWith('/admin/users/target-1/role')) return json(body, status);
            return json({ error: 'unexpected' }, 500);
          })()
        )
      );
      vi.stubGlobal('fetch', fetchMock);
      const events = userEvent.setup();
      renderUsers();
      const select = await screen.findByLabelText('Role for Target User');
      await events.selectOptions(select, 'admin');
      expect(await screen.findByRole('alert')).toHaveTextContent(message);
      expect(select).toHaveValue('user');
    }
  );

  it('only fetches a typed search after Apply search is submitted', async () => {
    const fetchMock = vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      if (url.pathname.endsWith('/auth/me'))
        return json({ user: admin, role: 'admin', capabilities: ['admin:access'] });
      if (url.pathname.endsWith('/auth/csrf')) return json({ csrf_token: 'csrf-token' });
      if (url.pathname.endsWith('/admin/users'))
        return json({ items: url.searchParams.get('q') === 'new query' ? [] : [target] });
      return json({ error: 'unexpected' }, 500);
    });
    vi.stubGlobal('fetch', fetchMock);
    const events = userEvent.setup();
    renderUsers();
    await screen.findByLabelText('Role for Target User');

    await events.type(screen.getByLabelText('Search by email or name'), 'new query');
    const searchRequests = () =>
      fetchMock.mock.calls.filter(([request]) =>
        new URL((request as Request).url).pathname.endsWith('/admin/users')
      );
    expect(searchRequests()).toHaveLength(1);

    await events.click(screen.getByRole('button', { name: 'Apply search' }));
    await screen.findByText('No users match this search.');
    expect(new URL((searchRequests()[1]?.[0] as Request).url).searchParams.get('q')).toBe(
      'new query'
    );
  });

  it('keeps the latest applied search when an earlier cancelled request resolves late', async () => {
    let resolveStale: ((response: Response) => void) | undefined;
    const fetchMock = vi.fn((request: Request) => {
      const url = new URL(request.url);
      if (url.pathname.endsWith('/auth/me'))
        return Promise.resolve(
          json({ user: admin, role: 'admin', capabilities: ['admin:access'] })
        );
      if (url.pathname.endsWith('/auth/csrf'))
        return Promise.resolve(json({ csrf_token: 'csrf-token' }));
      if (url.pathname.endsWith('/admin/users')) {
        if (url.searchParams.get('q') === 'first') {
          return new Promise<Response>((resolve) => {
            resolveStale = resolve;
          });
        }
        if (url.searchParams.get('q') === 'second')
          return Promise.resolve(
            json({ items: [{ ...target, id: 'second-1', name: 'Latest User' }] })
          );
        return Promise.resolve(json({ items: [target] }));
      }
      return Promise.resolve(json({ error: 'unexpected' }, 500));
    });
    vi.stubGlobal('fetch', fetchMock);
    const events = userEvent.setup();
    renderUsers();
    const search = await screen.findByLabelText('Search by email or name');

    await events.type(search, 'first');
    await events.click(screen.getByRole('button', { name: 'Apply search' }));
    await waitFor(() => expect(resolveStale).toBeDefined());
    await events.clear(search);
    await events.type(search, 'second');
    fireEvent.submit(search.closest('form')!);
    expect(await screen.findByText('Latest User')).toBeVisible();

    resolveStale?.(json({ items: [{ ...target, id: 'stale-1', name: 'Stale User' }] }));
    await waitFor(() => expect(screen.queryByText('Stale User')).not.toBeInTheDocument());
    expect(screen.getByText('Latest User')).toBeVisible();
  });

  it('hides rows and the empty state on a failed initial load, then retries with a new request', async () => {
    let listCalls = 0;
    const fetchMock = vi.fn((request: Request) => {
      const path = new URL(request.url).pathname;
      if (path.endsWith('/auth/me'))
        return Promise.resolve(
          json({ user: admin, role: 'admin', capabilities: ['admin:access'] })
        );
      if (path.endsWith('/auth/csrf')) return Promise.resolve(json({ csrf_token: 'csrf-token' }));
      if (path.endsWith('/admin/users')) {
        listCalls += 1;
        return Promise.resolve(
          listCalls <= 3 ? json({ error: 'unavailable' }, 503) : json({ items: [target] })
        );
      }
      return Promise.resolve(json({ error: 'unexpected' }, 500));
    });
    vi.stubGlobal('fetch', fetchMock);
    const events = userEvent.setup();
    renderUsers();

    expect(await screen.findByRole('alert')).toHaveTextContent('The role could not be changed.');
    expect(screen.queryByText('Target User')).not.toBeInTheDocument();
    expect(screen.queryByText('No users match this search.')).not.toBeInTheDocument();
    await events.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('Target User')).toBeVisible();
    expect(listCalls).toBe(4);
  });

  it('retries a failed applied query rather than retaining stale rows or the empty state', async () => {
    let searchCalls = 0;
    const fetchMock = vi.fn((request: Request) => {
      const url = new URL(request.url);
      if (url.pathname.endsWith('/auth/me'))
        return Promise.resolve(
          json({ user: admin, role: 'admin', capabilities: ['admin:access'] })
        );
      if (url.pathname.endsWith('/auth/csrf'))
        return Promise.resolve(json({ csrf_token: 'csrf-token' }));
      if (url.pathname.endsWith('/admin/users')) {
        if (url.searchParams.get('q') !== 'missing')
          return Promise.resolve(json({ items: [target] }));
        searchCalls += 1;
        return Promise.resolve(
          searchCalls <= 3 ? json({ error: 'unavailable' }, 503) : json({ items: [] })
        );
      }
      return Promise.resolve(json({ error: 'unexpected' }, 500));
    });
    vi.stubGlobal('fetch', fetchMock);
    const events = userEvent.setup();
    renderUsers();
    await screen.findByText('Target User');
    await events.type(screen.getByLabelText('Search by email or name'), 'missing');
    await events.click(screen.getByRole('button', { name: 'Apply search' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('The role could not be changed.');
    expect(screen.queryByText('Target User')).not.toBeInTheDocument();
    expect(screen.queryByText('No users match this search.')).not.toBeInTheDocument();
    await events.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('No users match this search.')).toBeVisible();
    expect(searchCalls).toBe(4);
  });

  it('removes self-admin controls after a successful self-demotion refreshes capabilities', async () => {
    const self = { ...admin, role: 'admin', created_at: '2026-01-01T00:00:00Z' };
    let refreshed = false;
    const fetchMock = vi.fn((request: Request) => {
      const path = new URL(request.url).pathname;
      if (path.endsWith('/auth/me'))
        return Promise.resolve(
          json(
            refreshed
              ? { user: self, role: 'moderator', capabilities: [] }
              : { user: self, role: 'admin', capabilities: ['admin:access'] }
          )
        );
      if (path.endsWith('/auth/csrf')) return Promise.resolve(json({ csrf_token: 'csrf-token' }));
      if (path.endsWith('/admin/users') && request.method === 'GET')
        return Promise.resolve(json({ items: [self] }));
      if (path.endsWith('/admin/users/admin-1/role')) {
        refreshed = true;
        return Promise.resolve(json({ user_id: self.id, role: 'moderator' }));
      }
      return Promise.resolve(json({ error: 'unexpected' }, 500));
    });
    vi.stubGlobal('fetch', fetchMock);
    const events = userEvent.setup();
    renderUsers();
    const select = await screen.findByLabelText('Role for Admin');
    await events.selectOptions(select, 'moderator');

    expect(
      await screen.findByText(
        'Roles are read-only for this account. Admin access is required to change them.'
      )
    ).toBeVisible();
    expect(screen.queryByLabelText('Role for Admin')).not.toBeInTheDocument();
    expect(screen.getByText('Moderator')).toBeVisible();
  });

  it('preserves self-admin capabilities when the role mutation fails', async () => {
    const self = { ...admin, role: 'admin', created_at: '2026-01-01T00:00:00Z' };
    const fetchMock = vi.fn((request: Request) => {
      const path = new URL(request.url).pathname;
      if (path.endsWith('/auth/me'))
        return Promise.resolve(json({ user: self, role: 'admin', capabilities: ['admin:access'] }));
      if (path.endsWith('/auth/csrf')) return Promise.resolve(json({ csrf_token: 'csrf-token' }));
      if (path.endsWith('/admin/users') && request.method === 'GET')
        return Promise.resolve(json({ items: [self] }));
      if (path.endsWith('/admin/users/admin-1/role'))
        return Promise.resolve(json({ error: 'final_admin' }, 409));
      return Promise.resolve(json({ error: 'unexpected' }, 500));
    });
    vi.stubGlobal('fetch', fetchMock);
    const events = userEvent.setup();
    renderUsers();
    const select = await screen.findByLabelText('Role for Admin');
    await events.selectOptions(select, 'moderator');

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'The final administrator cannot be demoted.'
    );
    expect(screen.getByLabelText('Role for Admin')).toHaveValue('admin');
    expect(
      screen.queryByText(
        'Roles are read-only for this account. Admin access is required to change them.'
      )
    ).not.toBeInTheDocument();
  });
});
