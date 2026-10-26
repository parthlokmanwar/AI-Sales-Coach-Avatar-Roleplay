import { useState } from 'react';
import { Lock, ArrowRight, AlertCircle, ShieldCheck } from 'lucide-react';
import { changePassword } from '../auth';
import '../components/Login.css';

const LOGO = '/posiview-logo.png';

// Shown right after login when the account is flagged must_reset_pw. If we know
// the password the user just logged in with (knownCurrent), we don't ask for it
// again — they only choose a new one.
export default function ForcePasswordReset({ user, knownCurrent, onDone, onSignOut }) {
  const [cur, setCur] = useState(knownCurrent || '');
  const [np, setNp] = useState('');
  const [cf, setCf] = useState('');
  const [err, setErr] = useState('');
  const [loading, setLoading] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setErr('');
    if (np !== cf) { setErr('New passwords do not match.'); return; }
    if (np.length < 8) { setErr('Password must be at least 8 characters.'); return; }
    setLoading(true);
    try { await changePassword(cur, np); onDone(); }
    catch (e2) { setErr(e2.message); setLoading(false); }
  };

  return (
    <div className="pvl-root">
      <div className="pvl-card" style={{ maxWidth: 460, gridTemplateColumns: '1fr', minHeight: 0 }}>
        <main className="pvl-form-wrap">
          <div className="pvl-form-logo" style={{ display: 'block' }}><img src={LOGO} alt="Posiview" /></div>
          <h2 className="pvl-h">Set a new password</h2>
          <p className="pvl-h-sub">For your security, please choose a new password before continuing.</p>
          <form onSubmit={submit}>
            {err && <div className="pvl-error"><AlertCircle size={16} /> {err}</div>}
            {!knownCurrent && (
              <div className="pvl-field">
                <label className="pvl-label">Current password</label>
                <div className="pvl-input-wrap"><span className="pvl-input-ic"><Lock size={17} /></span>
                  <input className="pvl-input" type="password" value={cur} onChange={(e) => setCur(e.target.value)} required /></div>
              </div>
            )}
            <div className="pvl-field">
              <label className="pvl-label">New password</label>
              <div className="pvl-input-wrap"><span className="pvl-input-ic"><Lock size={17} /></span>
                <input className="pvl-input" type="password" value={np} onChange={(e) => setNp(e.target.value)} placeholder="At least 8 characters" autoFocus required /></div>
            </div>
            <div className="pvl-field">
              <label className="pvl-label">Confirm new password</label>
              <div className="pvl-input-wrap"><span className="pvl-input-ic"><Lock size={17} /></span>
                <input className="pvl-input" type="password" value={cf} onChange={(e) => setCf(e.target.value)} required /></div>
            </div>
            <button className="pvl-submit" type="submit" disabled={loading}>
              {loading ? <><span className="pvl-spinner" /> Saving…</> : <>Set password <ArrowRight size={17} /></>}
            </button>
          </form>
          <div className="pvl-foot"><ShieldCheck size={14} /> Signed in as {user.email}</div>
          <div className="pvl-copy">
            <button onClick={onSignOut} style={{ background: 'none', border: 'none', color: 'inherit', cursor: 'pointer', textDecoration: 'underline', fontSize: 11 }}>Sign out</button>
          </div>
        </main>
      </div>
    </div>
  );
}
