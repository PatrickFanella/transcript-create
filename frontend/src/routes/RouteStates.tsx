import { Suspense } from 'react';
import type { ReactNode } from 'react';
import { Link, isRouteErrorResponse, useRouteError } from 'react-router-dom';

export function PageSuspense({ children }: { children: ReactNode }) {
  return (
    <Suspense fallback={<div className="p-6 text-muted">Loading page…</div>}>{children}</Suspense>
  );
}

export function ForbiddenPage() {
  return (
    <section className="mx-auto max-w-2xl p-8" role="alert">
      <div className="archive-eyebrow">403</div>
      <h1 className="mt-3 text-3xl font-semibold text-ink">Admin access required</h1>
      <p className="mt-3 text-muted">This area requires the admin capability.</p>
      <Link className="btn mt-6 inline-flex" to="/">
        Return home
      </Link>
    </section>
  );
}

export function NotFoundPage() {
  return (
    <section className="mx-auto max-w-2xl p-8">
      <div className="archive-eyebrow">404</div>
      <h1 className="mt-3 text-3xl font-semibold text-ink">Page not found</h1>
      <p className="mt-3 text-muted">The requested archive page does not exist.</p>
      <Link className="btn mt-6 inline-flex" to="/">
        Return home
      </Link>
    </section>
  );
}

export function RouteErrorPage() {
  const error = useRouteError();
  const message = isRouteErrorResponse(error)
    ? `${error.status} ${error.statusText}`
    : error instanceof Error
      ? error.message
      : 'The page could not be loaded.';
  return (
    <section className="mx-auto max-w-2xl p-8" role="alert">
      <div className="archive-eyebrow">Route error</div>
      <h1 className="mt-3 text-3xl font-semibold text-ink">Something interrupted this page</h1>
      <p className="mt-3 text-muted">{message}</p>
      <div className="mt-6 flex gap-3">
        <button className="btn" onClick={() => window.location.reload()}>
          Retry
        </button>
        <Link className="btn-ghost" to="/">
          Return home
        </Link>
      </div>
    </section>
  );
}
