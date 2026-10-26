import { useState, useEffect, memo } from 'react';
import {
  Mail, Lock, Eye, EyeOff, ArrowRight, ArrowLeft, AlertCircle,
  ShieldCheck, Sparkles, Headphones, ExternalLink,
} from 'lucide-react';
import { login as apiLogin, forgotPassword } from '../auth';
import './Login.css';

const LOGO = '/posiview-logo.png';

const FEATURES = [
  { icon: Sparkles,    text: 'AI-powered pitch generation & coaching' },
  { icon: Headphones,  text: 'Live call assistance & roleplay simulation' },
  { icon: ShieldCheck, text: 'Enterprise-grade, access-controlled workspace' },
];

const TYPING_SEQS = [
  { text: 'Secure Workspace',                                                                speed: 38 },
  { text: 'Connect better.... Convert faster!',                                              speed: 32 },
  { text: 'AI-powered pitch generation & coaching',                                          speed: 22 },
  { text: 'Live call assistance & roleplay simulation',                                      speed: 22 },
  { text: 'Enterprise-grade, access-controlled workspace',                                   speed: 22 },
  { text: 'Posiview Digital Technologies · Transforming Learning, Creating Experiences',     speed: 15 },
];
const PAUSE_MS = 90;

function Cursor({ show }) {
  return show ? <span className="pvl-cursor" aria-hidden="true" /> : null;
}

// Isolated so typewriter re-renders never touch the form inputs
const BrandPanel = memo(function BrandPanel() {
  const [stage,    setStage]    = useState(0);
  const [chars,    setChars]    = useState(0);
  const [revealed, setRevealed] = useState(TYPING_SEQS.map(() => ''));

  useEffect(() => {
    if (stage >= TYPING_SEQS.length) return;
    const { text, speed } = TYPING_SEQS[stage];
    if (chars < text.length) {
      const t = setTimeout(() => {
        setRevealed(prev => {
          const next = [...prev];
          next[stage] = text.slice(0, chars + 1);
          return next;
        });
        setChars(c => c + 1);
      }, speed);
      return () => clearTimeout(t);
    }
    const t = setTimeout(() => { setStage(s => s + 1); setChars(0); }, PAUSE_MS);
    return () => clearTimeout(t);
  }, [stage, chars]);

  return (
    <aside className="pvl-brand">
      <div className="pvl-brand-logo">
        <img src={LOGO} alt="Posiview" />
      </div>

      <div className="pvl-brand-body">
        <div className="pvl-eyebrow">
          <Lock size={13} />
          {revealed[0]}<Cursor show={stage === 0} />
        </div>

        <h1 className="pvl-brand-title">
          POSIVIEW EDGE <span>IQ</span>
        </h1>

        {stage >= 1 && (
          <p className="pvl-brand-sub">
            {revealed[1]}<Cursor show={stage === 1} />
          </p>
        )}

        <div className="pvl-features">
          {FEATURES.map(({ icon: Icon, text }, i) =>
            stage >= i + 2 && (
              <div className="pvl-feature" key={text}>
                <span className="pvl-feature-ic"><Icon size={15} /></span>
                {revealed[i + 2]}<Cursor show={stage === i + 2} />
              </div>
            )
          )}
        </div>

        {stage >= 5 && (
          <div className="pvl-brand-foot">
            {revealed[5]}<Cursor show={stage === 5} />
          </div>
        )}
      </div>
    </aside>
  );
});

export default function Login({ onSuccess, onBack }) {
  const [email,       setEmail]       = useState('');
  const [password,    setPassword]    = useState('');
  const [showPw,      setShowPw]      = useState(false);
  const [error,       setError]       = useState('');
  const [loading,     setLoading]     = useState(false);
  const [mode,        setMode]        = useState('signin'); // 'signin' | 'forgot'
  const [forgotSent,  setForgotSent]  = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (loading) return;
    setError('');
    setLoading(true);
    try {
      const user = await apiLogin(email.trim(), password);
      onSuccess?.(user, password);
    } catch (err) {
      setError(err.message || 'Sign in failed. Please try again.');
      setLoading(false);
    }
  };

  const handleForgot = async (e) => {
    e.preventDefault();
    if (loading) return;
    setError('');
    setLoading(true);
    try {
      await forgotPassword(email.trim());
      setForgotSent(true);
    } catch (err) {
      setError(err.message || 'Could not send reset link.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="pvl-root">
      <div className="pvl-card-wrap">
        <div className="pvl-card">

          <BrandPanel />

          {/* ── Form panel ── */}
          <main className="pvl-form-wrap">
            {onBack && (
              <button type="button" className="pvl-back" onClick={onBack}>
                <ArrowLeft size={15} /> Back to home
              </button>
            )}

            <div className="pvl-form-logo">
              <img src={LOGO} alt="Posiview" />
            </div>

            {mode === 'forgot' ? (
              <>
                <h2 className="pvl-h">Reset your password</h2>
                <p className="pvl-h-sub">Enter your email and we'll send you a reset link.</p>
                {forgotSent ? (
                  <>
                    <div className="pvl-error" style={{ color: '#047857', background: '#f0fdf4', borderColor: 'rgba(16,185,129,0.25)' }}>
                      <ShieldCheck size={16} /> If an account exists for that email, a reset link has been sent.
                    </div>
                    <button className="pvl-submit" type="button"
                      onClick={() => { setMode('signin'); setForgotSent(false); setError(''); }}>
                      Back to sign in
                    </button>
                  </>
                ) : (
                  <form onSubmit={handleForgot} noValidate>
                    {error && <div className="pvl-error" role="alert"><AlertCircle size={16} /> {error}</div>}
                    <div className="pvl-field">
                      <label className="pvl-label" htmlFor="pvl-femail">Email address</label>
                      <div className="pvl-input-wrap">
                        <span className="pvl-input-ic"><Mail size={17} /></span>
                        <input id="pvl-femail" className="pvl-input" type="email" placeholder="you@posiviewtech.com"
                          value={email} onChange={(e) => setEmail(e.target.value)} autoFocus required />
                      </div>
                    </div>
                    <button className="pvl-submit" type="submit" disabled={loading || !email}>
                      {loading ? <><span className="pvl-spinner" /> Sending…</> : <>Send reset link <ArrowRight size={17} /></>}
                    </button>
                    <button type="button" onClick={() => { setMode('signin'); setError(''); }}
                      style={{ display: 'block', margin: '14px auto 0', background: 'none', border: 'none', color: 'var(--pvl-muted)', cursor: 'pointer', fontSize: 12.5, fontWeight: 600 }}>
                      ← Back to sign in
                    </button>
                  </form>
                )}
              </>
            ) : (
              <>
                <h2 className="pvl-h">Welcome back</h2>
                <p className="pvl-h-sub">Sign in with your account to continue.</p>

                <form onSubmit={handleSubmit} noValidate>
                  {error && (
                    <div className="pvl-error" role="alert">
                      <AlertCircle size={16} /> {error}
                    </div>
                  )}

                  <div className="pvl-field">
                    <label className="pvl-label" htmlFor="pvl-email">Email address</label>
                    <div className="pvl-input-wrap">
                      <span className="pvl-input-ic"><Mail size={17} /></span>
                      <input
                        id="pvl-email"
                        className="pvl-input"
                        type="email"
                        autoComplete="username"
                        placeholder="you@posiviewtech.com"
                        value={email}
                        onChange={(e) => setEmail(e.target.value)}
                        autoFocus
                        required
                      />
                    </div>
                  </div>

                  <div className="pvl-field">
                    <label className="pvl-label" htmlFor="pvl-pw">Password</label>
                    <div className="pvl-input-wrap">
                      <span className="pvl-input-ic"><Lock size={17} /></span>
                      <input
                        id="pvl-pw"
                        className="pvl-input has-toggle"
                        type={showPw ? 'text' : 'password'}
                        autoComplete="current-password"
                        placeholder="Enter your password"
                        value={password}
                        onChange={(e) => setPassword(e.target.value)}
                        required
                      />
                      <button
                        type="button"
                        className="pvl-toggle"
                        onClick={() => setShowPw((s) => !s)}
                        aria-label={showPw ? 'Hide password' : 'Show password'}
                        tabIndex={-1}
                      >
                        {showPw ? <EyeOff size={17} /> : <Eye size={17} />}
                      </button>
                    </div>
                  </div>

                  <div style={{ textAlign: 'right', marginTop: -6, marginBottom: 14 }}>
                    <button type="button" onClick={() => { setMode('forgot'); setError(''); }}
                      style={{ background: 'none', border: 'none', color: 'var(--pvl-muted)', cursor: 'pointer', fontSize: 12.5, fontWeight: 600 }}>
                      Forgot password?
                    </button>
                  </div>

                  <button className="pvl-submit" type="submit" disabled={loading || !email || !password}>
                    {loading
                      ? <><span className="pvl-spinner" /> Signing in…</>
                      : <>Sign in <ArrowRight size={17} /></>}
                  </button>
                </form>
              </>
            )}

            <div className="pvl-foot">
              <ShieldCheck size={14} /> Authorized users only · Sessions are encrypted
            </div>
            <a className="pvl-link" href="https://posiviewtech.com/" target="_blank" rel="noopener noreferrer">
              posiviewtech.com <ExternalLink size={12} />
            </a>
          </main>

        </div>
      </div>
    </div>
  );
}
