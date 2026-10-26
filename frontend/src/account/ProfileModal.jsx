import { useState, useEffect } from 'react';
import { X, Check, AlertCircle, Loader } from 'lucide-react';
import { getProfile, updateProfile, changePassword } from '../auth';
import './account.css';

export default function ProfileModal({ user, onClose, onUserUpdated }) {
  const [profile, setProfile] = useState(null);
  const [name, setName] = useState(user?.name || '');
  const [savingName, setSavingName] = useState(false);
  const [pw, setPw] = useState({ old_password: '', new_password: '', confirm: '' });
  const [savingPw, setSavingPw] = useState(false);
  const [msg, setMsg] = useState(null); // {type, text}

  useEffect(() => {
    getProfile().then((p) => { setProfile(p); setName(p.name); }).catch(() => {});
    const h = (e) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [onClose]);

  const initial = (name || user?.email || '?').trim().charAt(0).toUpperCase();
  const roleLabel = { super_admin: 'Super Admin', company_admin: 'Company Admin', sales_agent: 'Sales Agent' }[profile?.role || user?.role] || (profile?.role || '');

  const saveName = async (e) => {
    e.preventDefault();
    setMsg(null); setSavingName(true);
    try {
      const u = await updateProfile(name.trim());
      onUserUpdated?.(u);
      setMsg({ type: 'ok', text: 'Profile updated.' });
    } catch (err) { setMsg({ type: 'error', text: err.message }); }
    finally { setSavingName(false); }
  };

  const savePw = async (e) => {
    e.preventDefault();
    setMsg(null);
    if (pw.new_password !== pw.confirm) { setMsg({ type: 'error', text: 'New passwords do not match.' }); return; }
    setSavingPw(true);
    try {
      await changePassword(pw.old_password, pw.new_password);
      setPw({ old_password: '', new_password: '', confirm: '' });
      setMsg({ type: 'ok', text: 'Password changed.' });
    } catch (err) { setMsg({ type: 'error', text: err.message }); }
    finally { setSavingPw(false); }
  };

  return (
    <div className="acct-overlay" onClick={onClose}>
      <div className="acct-modal" onClick={(e) => e.stopPropagation()}>
        <div className="acct-head">
          <h3>Your account</h3>
          <button className="acct-x" onClick={onClose}><X size={18} /></button>
        </div>
        <div className="acct-body">
          <div className="acct-id">
            <div className="acct-avatar">{initial}</div>
            <div>
              <div className="acct-id-name">{name || '—'}</div>
              <div className="acct-id-email">{profile?.email || user?.email}</div>
              <span className="acct-role">{roleLabel}{profile?.company_name ? ` · ${profile.company_name}` : ''}</span>
            </div>
          </div>

          {msg && (
            <div className={`acct-msg ${msg.type}`}>
              {msg.type === 'ok' ? <Check size={15} /> : <AlertCircle size={15} />} {msg.text}
            </div>
          )}

          <form onSubmit={saveName}>
            <div className="acct-section-label">Profile</div>
            <div className="acct-field">
              <label>Display name</label>
              <input className="acct-input" value={name} onChange={(e) => setName(e.target.value)} required />
            </div>
            <div className="acct-actions">
              <button className="acct-btn primary" disabled={savingName || !name.trim()}>
                {savingName ? <Loader size={15} className="spin" /> : <Check size={15} />} Save profile
              </button>
            </div>
          </form>

          <div className="acct-divider" />

          <form onSubmit={savePw}>
            <div className="acct-section-label">Change password</div>
            <div className="acct-field">
              <label>Current password</label>
              <input className="acct-input" type="password" autoComplete="current-password"
                value={pw.old_password} onChange={(e) => setPw((p) => ({ ...p, old_password: e.target.value }))} required />
            </div>
            <div className="acct-field">
              <label>New password</label>
              <input className="acct-input" type="password" autoComplete="new-password"
                value={pw.new_password} onChange={(e) => setPw((p) => ({ ...p, new_password: e.target.value }))} required />
            </div>
            <div className="acct-field">
              <label>Confirm new password</label>
              <input className="acct-input" type="password" autoComplete="new-password"
                value={pw.confirm} onChange={(e) => setPw((p) => ({ ...p, confirm: e.target.value }))} required />
            </div>
            <div className="acct-actions">
              <button className="acct-btn primary" disabled={savingPw}>
                {savingPw ? <Loader size={15} className="spin" /> : null} Update password
              </button>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
}
