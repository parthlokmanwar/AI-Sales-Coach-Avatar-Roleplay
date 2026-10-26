import { useState } from 'react';
import { LayoutDashboard, Building2, Users, FileText, ScrollText, LogOut, BarChart3, User, KeyRound, Calendar } from 'lucide-react';
import SuperAdminConsole from './SuperAdminConsole';
import CompanyAdminConsole from './CompanyAdminConsole';
import ProfileModal from '../account/ProfileModal';
import { useToast } from './ui';
import './admin.css';

const SUPER_NAV = [
  { id: 'overview', label: 'Overview', icon: LayoutDashboard },
  { id: 'analytics', label: 'Analytics', icon: BarChart3 },
  { id: 'companies', label: 'Companies', icon: Building2 },
  { id: 'logins', label: 'Sign-in log', icon: KeyRound },
  { id: 'audit', label: 'Audit log', icon: ScrollText },
];
const ADMIN_NAV = [
  { id: 'overview', label: 'Overview', icon: LayoutDashboard },
  { id: 'analytics', label: 'Analytics', icon: BarChart3 },
  { id: 'sessions', label: 'Sessions', icon: Calendar },
  { id: 'agents', label: 'Sales agents', icon: Users },
  { id: 'documents', label: 'Documents', icon: FileText },
  { id: 'logins', label: 'Sign-in log', icon: KeyRound },
  { id: 'audit', label: 'Audit log', icon: ScrollText },
];

export default function AdminApp({ user, onSignOut }) {
  const isSuper = user.role === 'super_admin';
  const nav = isSuper ? SUPER_NAV : ADMIN_NAV;
  // Deep-link support: /?view=<navId> opens that page directly (shareable links).
  const [page, setPage] = useState(() => {
    const v = new URLSearchParams(window.location.search).get('view');
    return nav.some((n) => n.id === v) ? v : 'overview';
  });
  const [profileOpen, setProfileOpen] = useState(false);
  const [acctUser, setAcctUser] = useState(user);
  const { show, node } = useToast();
  const Console = isSuper ? SuperAdminConsole : CompanyAdminConsole;
  const initial = (acctUser.name || acctUser.email || '?').trim().charAt(0).toUpperCase();

  return (
    <>
      <div className="av-root app-shell">
        <header className="topbar">
          <div className="topbar-brand">
            <div className="topbar-logo">PE</div>
            <div>
              <div className="topbar-title">Posiview Edge</div>
              <div className="topbar-subtitle">{isSuper ? 'Platform Console' : 'Company Console'}</div>
            </div>
          </div>
          <div className="topbar-meta">
            <div className="topbar-user">
              <button className="topbar-user-avatar" title="Your account" onClick={() => setProfileOpen(true)} style={{ border: 'none', cursor: 'pointer' }}>{initial}</button>
              <div className="topbar-user-info" onClick={() => setProfileOpen(true)} style={{ cursor: 'pointer' }}>
                <div className="topbar-user-name">{acctUser.name || 'Signed in'}</div>
                <div className="topbar-user-email">{acctUser.email}</div>
              </div>
              <button className="topbar-profile" onClick={() => setProfileOpen(true)} title="Account settings"><User size={16} /></button>
              <button className="topbar-signout" onClick={onSignOut} title="Sign out"><LogOut size={16} /></button>
            </div>
          </div>
        </header>

        <div className="app-body">
          <nav className="sidebar">
            <div className="sidebar-section-label">{isSuper ? 'Platform' : 'Manage'}</div>
            {nav.map((item) => {
              const Icon = item.icon;
              return (
                <button key={item.id}
                  className={`nav-item${page === item.id ? ' active' : ''}`}
                  onClick={() => setPage(item.id)}>
                  <span className="nav-item-icon"><Icon size={17} /></span>
                  <span className="nav-item-label">{item.label}</span>
                </button>
              );
            })}
          </nav>

          <main className="main">
            <div className="main-inner">
              <Console page={page} showToast={show} user={user} />
            </div>
          </main>
        </div>
      </div>
      {node}
      {profileOpen && (
        <ProfileModal
          user={acctUser}
          onClose={() => setProfileOpen(false)}
          onUserUpdated={(u) => setAcctUser((prev) => ({ ...prev, ...u }))}
        />
      )}
    </>
  );
}
