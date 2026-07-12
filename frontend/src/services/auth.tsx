import { createContext, useContext, useEffect, useState } from 'react';
import { buildApiUrl, http } from './api';

type User = {
  id: string;
  email?: string | null;
  name?: string | null;
  avatar_url?: string | null;
  plan?: string | null;
};

type AuthEnvelope = {
  user: User | null;
  role?: string | null;
  capabilities?: string[];
};

export type AuthStatus = 'loading' | 'authenticated' | 'anonymous' | 'error';

type AuthState = {
  user: User | null;
  loading: boolean;
  status: AuthStatus;
  error: string | null;
  role: string | null;
  capabilities: string[];
  login: () => void;
  loginTwitch: () => void;
  logout: () => Promise<void>;
};

const AuthCtx = createContext<AuthState | null>(null);

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Authentication request failed';
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [status, setStatus] = useState<AuthStatus>('loading');
  const [error, setError] = useState<string | null>(null);
  const [role, setRole] = useState<string | null>(null);
  const [capabilities, setCapabilities] = useState<string[]>([]);
  useEffect(() => {
    let active = true;
    void http
      .get('auth/me')
      .json<AuthEnvelope>()
      .then((response) => {
        if (!active) return;
        setUser(response.user);
        setRole(response.role ?? null);
        setCapabilities(response.capabilities ?? []);
        setError(null);
        setStatus(response.user ? 'authenticated' : 'anonymous');
      })
      .catch((requestError: unknown) => {
        if (!active) return;
        setUser(null);
        setRole(null);
        setCapabilities([]);
        setError(errorMessage(requestError));
        setStatus('error');
      });
    return () => {
      active = false;
    };
  }, []);
  return (
    <AuthCtx.Provider
      value={{
        user,
        loading: status === 'loading',
        status,
        error,
        role,
        capabilities,
        login: () => {
          window.location.href = buildApiUrl('auth/login/google');
        },
        loginTwitch: () => {
          window.location.href = buildApiUrl('auth/login/twitch');
        },
        logout: async () => {
          try {
            await http.post('auth/logout').json();
            setUser(null);
            setRole(null);
            setCapabilities([]);
            setError(null);
            setStatus('anonymous');
          } catch (requestError: unknown) {
            setError(errorMessage(requestError));
            setStatus('error');
          }
        },
      }}
    >
      {children}
    </AuthCtx.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth() {
  const ctx = useContext(AuthCtx);
  if (!ctx) throw new Error('AuthProvider missing');
  return ctx;
}
