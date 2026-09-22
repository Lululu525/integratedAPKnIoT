import { useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router';
import './Login.css';

export default function ResetPassword() {
  const navigate = useNavigate();
  // Prefilled from the URL when the link carried it, which is the shape a mail
  // transport would use. Typed in by hand otherwise, which is the shape this
  // deployment actually has.
  const [params] = useSearchParams();
  const [token, setToken] = useState(params.get('token') ?? '');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);

    try {
      const res = await fetch('/backend/api/auth/reset-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token, password }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        // Two different 400s share this route: a token that does not verify,
        // and a password the rules refuse. Only the second carries a reason,
        // and it is nested, so read it rather than printing the whole object.
        const detail = body?.detail;
        throw new Error(
          typeof detail === 'string'
            ? '驗證碼無效或已過期，請重新申請一次。'
            : (detail?.reason ?? `重設失敗（HTTP ${res.status}）`),
        );
      }
      navigate('/login');
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
          <h1 className="text-xl font-bold text-primary">設定新密碼</h1>
          <p className="text-sm text-secondary">
            設定完成後，這個帳號其他還開著的登入階段都會一併登出。
          </p>
        </div>

        <form className="login-form" onSubmit={handleSubmit}>
          <div className="form-group">
            <label className="form-label">重設驗證碼</label>
            <textarea
              className="form-input font-mono"
              rows={3}
              value={token}
              onChange={e => setToken(e.target.value)}
              style={{ resize: 'vertical' }}
              required
            />
          </div>

          <div className="form-group">
            <label className="form-label">新密碼</label>
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
              <span className="alert-title">重設失敗：</span>
              {error}
            </div>
          )}

          <button
            type="submit"
            className="btn btn-primary"
            style={{ width: '100%', marginTop: '0.5rem', padding: '0.8rem' }}
            disabled={submitting}
          >
            設定新密碼
          </button>
        </form>
      </div>

      <p className="login-footer text-xs text-secondary">
        <Link to="/login">回到登入</Link>
      </p>
    </div>
  );
}
