import { Link, Outlet } from 'react-router-dom';
import Protected from '../Protected';
import { useAuth } from '../../services/auth';
import { ForbiddenPage } from '../RouteStates';

function ProtectedAdmin({ children }: { children: React.ReactNode }) {
  const { user, loading, capabilities } = useAuth();
  if (loading) return <div className="p-6">Loading…</div>;
  if (!user) return <Protected>{children}</Protected>;
  if (!capabilities.includes('admin:access')) return <ForbiddenPage />;
  return <>{children}</>;
}

export default function AdminLayout() {
  return (
    <ProtectedAdmin>
      <div className="mx-auto max-w-7xl space-y-6 p-4 md:p-6">
        <nav className="flex flex-wrap gap-2 border-b border-border pb-4">
          <Link className="nav-link rounded-md px-3 py-2" to="/admin/dashboard">
            Dashboard
          </Link>
          <Link className="nav-link rounded-md px-3 py-2" to="/admin/events">
            Events
          </Link>
          <Link className="nav-link rounded-md px-3 py-2" to="/admin/users">
            Users
          </Link>
          <Link className="nav-link rounded-md px-3 py-2" to="/admin/periods">
            Periods
          </Link>
          <Link className="nav-link rounded-md px-3 py-2" to="/admin/metadata">
            Metadata
          </Link>
          <Link className="nav-link rounded-md px-3 py-2" to="/admin/labels">
            Labels
          </Link>
        </nav>
        <Outlet />
      </div>
    </ProtectedAdmin>
  );
}
