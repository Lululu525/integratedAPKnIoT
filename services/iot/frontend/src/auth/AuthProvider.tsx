import { useCallback, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { AuthContext } from './context';
import type { Account, Session } from './context';

const STORAGE_KEY = 'ota.session';

// Renew this far ahead of expiry rather than waiting for the 401. A request
// that leaves with thirty seconds left on the token can still arrive after it
// has expired, and the retry path is a second round trip for something the
// clock already knew.
const RENEW_MARGIN_MS = 60_000;

interface SessionResponse {
  access_token: string;
  expires_in: number;
  refresh_token: string;
  user: { id: number; email: string; has_public_key: boolean };
}

function toAccount(user: SessionResponse['user']): Account {
  return { id: user.id, email: user.email, hasPublicKey: !!user.has_public_key };
}

function toSession(body: SessionResponse): Session {
  return {
    accessToken: body.access_token,
    refreshToken: body.refresh_token,
    expiresAt: Date.now() + body.expires_in * 1000,
    account: toAccount(body.user),
  };
}

// The inverse of storeSession. An entry that is not the right shape is dropped
// rather than repaired. An expired access token is not a reason to drop one:
// the handle beside it is what the session actually rests on, and the first
// request will trade it in.
function readStoredSession(): Session | null {
  const stored = sessionStorage.getItem(STORAGE_KEY);
  if (!stored) return null;

  try {
    const { accessToken, refreshToken, expiresAt, account } = JSON.parse(stored);
    if (typeof accessToken !== 'string' || typeof refreshToken !== 'string') {
      throw new Error('bad shape');
    }
    if (typeof expiresAt !== 'number' || typeof account?.email !== 'string') {
      throw new Error('bad shape');
    }
    return { accessToken, refreshToken, expiresAt, account };
  } catch {
    sessionStorage.removeItem(STORAGE_KEY);
    return null;
  }
}

function storeSession(session: Session | null) {
  if (session) sessionStorage.setItem(STORAGE_KEY, JSON.stringify(session));
  else sessionStorage.removeItem(STORAGE_KEY);
}

async function errorDetail(res: Response, fallback: string): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body.detail === 'string') return body.detail;
  } catch {
    // Non-JSON body; use the fallback.
  }
  return fallback;
}

export default function AuthProvider({ children }: { children: ReactNode }) {
  // Restored during the first render, not in an effect: RequireAuth redirects
  // to /login the moment it sees a null session, and would win that race.
  const [session, setSessionState] = useState<Session | null>(readStoredSession);

  // authFetch is handed to pages as a stable callback, so it cannot close over
  // `session` without going stale the first time the token rotates. The ref is
  // what it reads instead.
  const sessionRef = useRef<Session | null>(session);
  const renewalRef = useRef<Promise<Session | null> | null>(null);

  const setSession = useCallback((next: Session | null) => {
    sessionRef.current = next;
    storeSession(next);
    setSessionState(next);
  }, []);

  // One renewal at a time. Handles are single-use, so two requests noticing an
  // expiring token at the same moment would each spend one, and whichever
  // landed second would be rejected as a replay and log the user out.
  const renew = useCallback(async (): Promise<Session | null> => {
    if (renewalRef.current) return renewalRef.current;

    const current = sessionRef.current;
    if (!current) return null;

    const attempt = (async () => {
      try {
        const res = await fetch('/backend/api/auth/refresh', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ refresh_token: current.refreshToken }),
        });
        if (!res.ok) {
          setSession(null);
          return null;
        }
        const next = toSession(await res.json());
        setSession(next);
        return next;
      } catch {
        // A network failure is not an expired session. Keep what we have so a
        // dropped connection does not sign the operator out mid-upload.
        return null;
      } finally {
        renewalRef.current = null;
      }
    })();

    renewalRef.current = attempt;
    return attempt;
  }, [setSession]);

  const authFetch = useCallback(
    async (input: string, init?: RequestInit): Promise<Response> => {
      let current = sessionRef.current;
      if (!current) throw new Error('Not signed in');

      if (current.expiresAt - Date.now() < RENEW_MARGIN_MS) {
        current = (await renew()) ?? current;
      }

      const send = (active: Session) =>
        fetch(input, {
          ...init,
          headers: { ...(init?.headers ?? {}), Authorization: `Bearer ${active.accessToken}` },
        });

      const res = await send(current);
      if (res.status !== 401) return res;

      // The clock said the token was fine and the server disagreed: a restarted
      // backend, a revoked account, or a token minted before a clock change.
      // One retry, then let the 401 through.
      const renewed = await renew();
      return renewed ? send(renewed) : res;
    },
    [renew],
  );

  const login = useCallback(
    async (email: string, password: string) => {
      // Form-encoded, and the field is called `username`: the login route takes
      // OAuth2's password form, whose field names are fixed by the standard
      // even when the identity in them is an address.
      const body = new URLSearchParams({ username: email, password });
      const res = await fetch('/backend/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body,
      });
      if (!res.ok) {
        throw new Error(await errorDetail(res, `Login failed (HTTP ${res.status})`));
      }
      setSession(toSession(await res.json()));
    },
    [setSession],
  );

  const setHasPublicKey = useCallback(
    (value: boolean) => {
      const current = sessionRef.current;
      if (!current) return;
      setSession({ ...current, account: { ...current.account, hasPublicKey: value } });
    },
    [setSession],
  );

  const logout = useCallback(async () => {
    const current = sessionRef.current;
    setSession(null);
    if (!current) return;
    // Best effort, and after the local state is already cleared. The handle is
    // worth destroying server-side, but a failed request must not leave someone
    // who pressed logout still looking at the dashboard.
    try {
      await fetch('/backend/api/auth/logout', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: current.refreshToken }),
      });
    } catch {
      // Nothing to do: the credential is gone from this browser either way.
    }
  }, [setSession]);

  const value = useMemo(
    () => ({ session, login, logout, authFetch, setHasPublicKey }),
    [session, login, logout, authFetch, setHasPublicKey],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
