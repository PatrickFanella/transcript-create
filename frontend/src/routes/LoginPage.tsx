import { useAuth } from '../services';

export default function LoginPage() {
  const { login, loginTwitch, status, error } = useAuth();
  return (
    <div className="mx-auto max-w-sm space-y-5">
      <h1 className="page-title">Sign in</h1>
      <p className="text-muted">
        Continue with Google or Twitch to synchronize saved moments, searches, and account access.
      </p>
      {status === 'error' && error && (
        <div className="alert-warning" role="alert">
          {error}
        </div>
      )}
      <div className="grid gap-3">
        <button className="btn min-h-11" type="button" onClick={login}>
          Continue with Google
        </button>
        <button className="btn-ghost min-h-11" type="button" onClick={loginTwitch}>
          Continue with Twitch
        </button>
      </div>
    </div>
  );
}
