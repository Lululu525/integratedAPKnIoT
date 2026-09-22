import { useState } from 'react';
import { Link } from 'react-router';
import './Login.css';

export default function ForgotPassword() {
  const [email, setEmail] = useState('');
  const [sent, setSent] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);

    try {
      const res = await fetch('/backend/api/auth/forgot-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email }),
      });
      // 202 whether or not the address has an account. The screen says the same
      // thing either way for the same reason the server does: an answer that
      // differed would turn this form into a way to find out who has an account.
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setSent(true);
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
          <h1 className="text-xl font-bold text-primary">重設密碼</h1>
          <p className="text-sm text-secondary">
            填入帳號的電子郵件，我們會產生一組重設用的驗證碼。
          </p>
        </div>

        {sent ? (
          <div className="alert alert-info">
            <span className="alert-title">已送出。</span>
            這台伺服器還沒有接寄信服務，驗證碼只會寫進後端的 log。請找有伺服器權限的人把它讀出來給你，
            再到<Link to="/reset-password"> 重設頁面 </Link>填入。
          </div>
        ) : (
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

            {error && (
              <div className="alert alert-error">
                <span className="alert-title">送出失敗：</span>
                {error}
              </div>
            )}

            <button
              type="submit"
              className="btn btn-primary"
              style={{ width: '100%', marginTop: '0.5rem', padding: '0.8rem' }}
              disabled={submitting}
            >
              送出
            </button>
          </form>
        )}
      </div>

      <p className="login-footer text-xs text-secondary">
        想起來了？<Link to="/login">回到登入</Link>
      </p>
    </div>
  );
}
