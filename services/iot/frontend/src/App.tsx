import './App.css'
import { Routes, Route, Link, Navigate, useLocation } from 'react-router'
import type { ReactNode } from 'react';
import DeviceList from './pages/DeviceList';
import Firmware from './pages/Firmware';
import ForgotPassword from './pages/ForgotPassword';
import Register from './pages/Register';
import Login from './pages/Login';
import ResetPassword from './pages/ResetPassword';
import { useAuth } from './auth/context';
import { useState, useEffect } from 'react';

const SIGNED_OUT_PATHS = new Set(['/login', '/register', '/forgot-password', '/reset-password']);

function SiderBar() {
  const { pathname } = useLocation();
  const { session, logout } = useAuth();
  const [isCollapsed, setIsCollapsed] = useState(() => window.innerWidth < 960);

  useEffect(() => {
    const mediaQuery = window.matchMedia('(max-width: 960px)');
    const handleMediaChange = (e: MediaQueryListEvent) => {
      setIsCollapsed(e.matches);
    };

    if (mediaQuery.addEventListener) {
      mediaQuery.addEventListener('change', handleMediaChange);
    } else {
      mediaQuery.addListener(handleMediaChange);
    }

    return () => {
      if (mediaQuery.removeEventListener) {
        mediaQuery.removeEventListener('change', handleMediaChange);
      } else {
        mediaQuery.removeListener(handleMediaChange);
      }
    };
  }, []);

  // Every screen that is reachable without a session, not just the login
  // form. A sidebar offering the fleet to someone who cannot load it is worse
  // than no sidebar.
  if (SIGNED_OUT_PATHS.has(pathname)) return null;

  const navItems = [
    { to: '/', index: '01', label: '韌體管理', hint: 'Firmware overview' },
    { to: '/devices', index: '02', label: '裝置監控', hint: 'Connected ESP32 fleet' },
  ];

  function isActivePath(target: string) {
    if (target === '/') return pathname === '/';
    return pathname === target;
  }

  return (
    <>
      {!isCollapsed && <div className="sidebar-backdrop" onClick={() => setIsCollapsed(true)} />}
      <aside className={`sidebar ${isCollapsed ? 'is-collapsed' : ''}`}>
        <div className="sidebar-inner">
          <div className="sidebar-brand">
            <div className="sidebar-mark font-mono text-xs font-bold text-inverse" aria-hidden="true">ESP</div>
            <div className="sidebar-brand-copy">
              <span className="sidebar-title text-base font-medium text-primary font-mono">ESPFleet</span>
              <span className="sidebar-subtitle">Firmware OTA Control Center</span>
            </div>
          </div>

          <nav className="sidebar-nav" aria-label="Primary navigation">
            {navItems.map(item => (
              <Link
                key={item.to}
                to={item.to}
                className={`sidebar-link${isActivePath(item.to) ? ' is-active' : ''}`}
              >
                <span className="sidebar-link-index text-xs font-medium text-primary font-mono">{item.index}</span>
                <span className="sidebar-link-copy">
                  <span className="sidebar-link-label text-sm font-medium font-mono">{item.label}</span>
                  <span className="sidebar-link-hint">{item.hint}</span>
                </span>
              </Link>
            ))}
          </nav>

          <div className="sidebar-footer">
            <div className="sidebar-account">
              {session ? (
                <>
                  <strong className="sidebar-account-name text-sm font-medium text-primary font-mono">{session.account.email}</strong>
                </>
              ) : (
                <span className="sidebar-account-name text-sm font-medium text-primary font-mono">Guest</span>
              )}
            </div>

            <div className="sidebar-actions">
              {session ? (
                <button type="button" className="sidebar-action sidebar-logout text-lg font-medium text-primary font-mono" onClick={logout}>
                  <span>登出</span>
                </button>
              ) : (
                <Link to="/login" className="sidebar-action sidebar-login text-lg font-medium text-primary font-mono">
                  Login
                </Link>
              )}
            </div>
          </div>
        </div>
        <button
          type="button"
          className="sidebar-toggle"
          onClick={() => setIsCollapsed(!isCollapsed)}
          title={isCollapsed ? "展開選單" : "收合選單"}
        >
          {isCollapsed ? ">" : "<"}
        </button>
      </aside>
    </>
  );
}

function RequireAuth({ children }: { children: ReactNode }) {
  const { session } = useAuth();
  return session ? children : <Navigate to="/login" replace />;
}

function App() {
  return (
    <>
      <SiderBar />
      <main className="content-shell">
        <Routes>
          <Route
            index
            element={
              <RequireAuth>
                <Firmware />
              </RequireAuth>
            }
          />
          <Route
            path="/upload"
            element={
              <RequireAuth>
                <Navigate to='/' replace />
              </RequireAuth>
            }
          />
          <Route
            path="/devices"
            element={
              <RequireAuth>
                <DeviceList />
              </RequireAuth>
            }
          />
          <Route path="/login" element={<Login />} />
          <Route path="/register" element={<Register />} />
          <Route path="/forgot-password" element={<ForgotPassword />} />
          <Route path="/reset-password" element={<ResetPassword />} />
        </Routes>
      </main>
    </>
  )
}

export default App
