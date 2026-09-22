import { createContext, useContext } from 'react';

// Dashboard session state.
//
// Two credentials, with different jobs. The access token is a short JWT that
// every request carries. The refresh handle is long-lived and single-use, and
// exists only to mint the next access token, which is what lets a session
// outlive the hour rather than dropping a login form into the middle of an
// upload.
//
// Both live in sessionStorage: they survive a reload and die with the tab. Not
// localStorage, and the fortnight-long handle is the reason rather than an
// exception to it. Writing a credential that lives that long to disk buys only
// that a closed browser comes back signed in.

// No role. What an account may do is decided by what it owns: every list is
// scoped to it server-side, and there is nothing it can reach that it did not
// create. The UI therefore has no permission to reflect.
export interface Account {
  id: number;
  email: string;
  // The server verifies uploads against this account's public key and holds no
  // key of its own, so without one there is nothing publishing could be
  // checked against. The upload form says so rather than letting someone fill
  // it in and collect a 400 at the end.
  hasPublicKey: boolean;
}

export interface Session {
  accessToken: string;
  refreshToken: string;
  // Integration-only guest sessions use a real scoped backend credential so
  // the dashboard works immediately, but they are not presented as a signed-
  // in person in the UI. A normal login always sets this to false.
  isGuest?: boolean;
  // Absolute, in epoch milliseconds, computed from the `expires_in` the server
  // states. Read off the response rather than decoded out of the JWT: the
  // browser carries that token, it does not interpret it.
  expiresAt: number;
  account: Account;
}

export interface AuthContextValue {
  session: Session | null;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  // Called after the public key is set, so the upload form stops saying the
  // account cannot publish without a reload.
  setHasPublicKey: (value: boolean) => void;
  // The only way a page should reach an authenticated endpoint. It renews a
  // token that is about to expire, retries once on a 401, and serializes
  // concurrent renewals so a page firing three requests at once does not spend
  // three single-use handles and lose two of them.
  authFetch: (input: string, init?: RequestInit) => Promise<Response>;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
