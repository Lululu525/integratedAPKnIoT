import { useState } from 'react';
import { Link, useNavigate } from 'react-router';
import { useAuth } from '../auth/context';
import './Login.css';

export default function Register() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);

    try {
      const res = await fetch('/backend/api/auth/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      });

      if (!res.ok) {
        const body = await res.json().catch(() => null);
        const detail = body?.detail;
        // The register route answers 400 for two different things: the address
        // is taken, and the password was refused. Only the second nests a
        // reason, and the first is a bare code rather than a sentence.
        if (detail === 'REGISTER_USER_ALREADY_EXISTS') {
          throw new Error('這個電子郵件已經有帳號了。');
        }
        throw new Error(detail?.reason ?? `註冊失敗（HTTP ${res.status}）`);
      }

      // Straight in. The account is usable the moment it exists, and making
      // someone retype what they just typed buys nothing.
      await login(email, password);
      navigate('/');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Request failed');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="login-page">
      <div className="login-brand">
        <div className="login-logo font-mono text-xs font-bold text-inverse">ESP</div>
        <div className="login-brand-text">
          <div className="login-brand-title text-2xl font-bold text-primary font-mono">ESPFleet</div>
          <div className="login-brand-subtitle text-sm text-secondary font-mono">韌體發布與裝置監控</div>
        </div>
      </div>

      <div className="card login-card">
        <div className="login-header">
          <h1 className="text-xl font-bold text-primary">建立帳號</h1>
        </div>

        <form className="login-form" onSubmit={handleSubmit}>
          <div className="form-group">
            <label className="form-label">電子郵件</label>
            <input
              type="email"
              className="form-input"
              value={email}
              onChange={e => setEmail(e.target.value)}
              autoComplete="username"
              required
            />
          </div>

          <div className="form-group">
            <label className="form-label">密碼</label>
            <input
              type="password"
              className="form-input"
              value={password}
              onChange={e => setPassword(e.target.value)}
              autoComplete="new-password"
              required
            />
            <span className="form-help">至少 8 個字元。</span>
          </div>

          {error && (
            <div className="alert alert-error">
              <span className="alert-title">註冊失敗：</span>
              {error}
            </div>
          )}

          <button
            type="submit"
            className="btn btn-primary"
            style={{ width: '100%', marginTop: '0.5rem', padding: '0.8rem' }}
            disabled={submitting}
          >
            建立帳號
          </button>
        </form>
      </div>

      <p className="login-footer text-xs text-secondary">
        已經有帳號了？<Link to="/login">直接登入</Link>
      </p>
    </div>
  );
}
