import { useState } from 'react';
import { Lock, ArrowRight, AlertCircle, CheckCircle2 } from 'lucide-react';
import { resetPassword } from '../auth';
import '../components/Login.css';

const LOGO = '/posiview-logo.png';

// Public page reached via /?reset_token=<token> (the link from forgot-password).
export default function ResetPassword({ token, onDone }) {
  const [np, setNp] = useState('');
  const [cf, setCf] = useState('');
  const [err, setErr] = useState('');
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setErr('');
    if (np !== cf) { setErr('Passwords do not match.'); return; }
    if (np.length < 8) { setErr('Password must be at least 8 characters.'); return; }
    setLoading(true);
    try { await resetPassword(token, np); setDone(true); }
    catch (e2) { setErr(e2.message); }
    finally { setLoading(false); }
  };

  return (
    <div className="pvl-root">
      <div className="pvl-card" style={{ maxWidth: 460, gridTemplateColumns: '1fr', minHeight: 0 }}>
        <main className="pvl-form-wrap">
          <div className="pvl-form-logo" style={{ display: 'block' }}><img src={LOGO} alt="Posiview" /></div>
          {done ? (
            <>
              <h2 className="pvl-h"><CheckCircle2 size={22} style={{ verticalAlign: -4, color: 'var(--pvl-green-deep, #059669)' }} /> Password reset</h2>
              <p className="pvl-h-sub">Your password has been updated. You can now sign in with it.</p>
              <button className="pvl-submit" onClick={onDone}>Go to sign in <ArrowRight size={17} /></button>
            </>
          ) : (
            <>
              <h2 className="pvl-h">Choose a new password</h2>
              <p className="pvl-h-sub">Enter a new password for your account.</p>
              <form onSubmit={submit}>
                {err && <div className="pvl-error"><AlertCircle size={16} /> {err}</div>}
                <div className="pvl-field">
                  <label className="pvl-label">New password</label>
                  <div className="pvl-input-wrap"><span className="pvl-input-ic"><Lock size={17} /></span>
                    <input className="pvl-input" type="password" value={np} onChange={(e) => setNp(e.target.value)} placeholder="At least 8 characters" autoFocus required /></div>
                </div>
                <div className="pvl-field">
                  <label className="pvl-label">Confirm password</label>
                  <div className="pvl-input-wrap"><span className="pvl-input-ic"><Lock size={17} /></span>
                    <input className="pvl-input" type="password" value={cf} onChange={(e) => setCf(e.target.value)} required /></div>
                </div>
                <button className="pvl-submit" type="submit" disabled={loading}>
                  {loading ? <><span className="pvl-spinner" /> Saving…</> : <>Reset password <ArrowRight size={17} /></>}
                </button>
              </form>
            </>
          )}
        </main>
      </div>
    </div>
  );
}
