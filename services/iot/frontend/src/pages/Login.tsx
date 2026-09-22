import { useState } from 'react';
import { Link, useNavigate } from 'react-router';
import { useAuth } from '../auth/context';
import './Login.css';

export default function Login() {
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
          <h1 className="text-xl font-bold text-primary">登入控制台</h1>
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
            <div className="login-password-label">
              <label className="form-label">密碼</label>
              <Link to="/forgot-password" className="login-forgot text-xs">忘記密碼？</Link>
            </div>
            <input
              type="password"
              className="form-input"
              value={password}
              onChange={e => setPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
          </div>

          {error && (
            <div className="alert alert-error">
              <span className="alert-title">登入失敗：</span>
              {error}
            </div>
          )}

          <button type="submit" className="btn btn-primary" style={{ width: '100%', marginTop: '0.5rem', padding: '0.8rem' }} disabled={submitting}>
            登入
          </button>
        </form>
      </div>

      <p className="login-footer text-xs text-secondary">
        還沒有帳號？<Link to="/register">建立一個</Link>
      </p>
    </div>
  );
}
