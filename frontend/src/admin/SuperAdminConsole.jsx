import { useState, useEffect, useCallback } from 'react';
import {
  Building2, Users, ShieldCheck, Layers, Plus, Power, Trash2, Eye,
  UserPlus, RefreshCw, Sparkles, MessageSquare, Headphones, Award,
} from 'lucide-react';
import { platform } from './api';
import {
  StatCard, Badge, DataTable, SearchBar, Pagination, Modal, Button, Field, CredsBox, Spinner,
  ActivityChart, MiniBar,
} from './ui';
import { LoginHistoryPage } from './SharedPages';

const fmtDate = (iso) => (iso ? new Date(iso).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' }) : '—');

// ── Overview ──────────────────────────────────────────────────────────────────
function Overview() {
  const [stats, setStats] = useState(null);
  const [recent, setRecent] = useState([]);
  useEffect(() => {
    platform.stats().then((d) => setStats(d.stats)).catch(() => {});
    platform.companies({ page_size: 5 }).then((d) => setRecent(d.items)).catch(() => {});
  }, []);
  return (
    <div className="av-page">
      <div className="av-page-head">
        <div><div className="av-page-title">Platform overview</div>
          <div className="av-page-sub">Activity across all companies on Posiview Edge</div></div>
      </div>
      <div className="av-stats-grid">
        <StatCard icon={Building2} label="Companies" value={stats ? stats.companies : '—'} />
        <StatCard icon={ShieldCheck} label="Active companies" value={stats ? stats.active_companies : '—'} />
        <StatCard icon={Users} label="Sales agents" value={stats ? stats.agents : '—'} />
        <StatCard icon={Layers} label="Total seats" value={stats ? stats.total_seats : '—'} />
      </div>
      <div className="av-card">
        <div className="av-card-head"><span className="av-card-title">Recent companies</span></div>
        <DataTable
          columns={[
            { key: 'name', label: 'Company', render: (r) => <div><div className="av-cell-strong">{r.name}</div><div className="av-cell-sub">{r.slug}</div></div> },
            { key: 'agent_count', label: 'Agents', render: (r) => r.agent_count ?? 0 },
            { key: 'status', label: 'Status', render: (r) => <Badge status={r.status} /> },
            { key: 'created_at', label: 'Created', render: (r) => fmtDate(r.created_at) },
          ]}
          rows={recent} loading={false} empty="No companies yet — create the first one."
        />
      </div>
    </div>
  );
}

// ── Create company modal ────────────────────────────────────────────────────────
function CreateCompanyModal({ onClose, onCreated, showToast }) {
  const [form, setForm] = useState({ name: '', admin_email: '', admin_name: '', max_seats: 25 });
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  const submit = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      const d = await platform.createCompany({
        name: form.name, admin_email: form.admin_email, admin_name: form.admin_name,
        max_seats: Number(form.max_seats) || 25,
      });
      setResult(d.company);
      onCreated();
    } catch (err) { showToast(err.message, 'error'); }
    finally { setLoading(false); }
  };

  if (result) {
    return (
      <Modal title="Company created" onClose={onClose}
        footer={<Button onClick={onClose}>Done</Button>}>
        <p style={{ fontSize: 13, color: 'var(--av-muted)', marginBottom: 14 }}>
          <strong>{result.name}</strong> is ready. Share these admin credentials securely — the
          password is shown only once and must be changed on first sign-in.
        </p>
        <CredsBox email={result.admin.email} password={result.admin.temp_password} />
      </Modal>
    );
  }

  return (
    <Modal title="New company" onClose={onClose}
      footer={<>
        <Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button onClick={submit} loading={loading}>Create company</Button>
      </>}>
      <form onSubmit={submit}>
        <Field label="Company name"><input className="av-input" value={form.name} onChange={(e) => set('name', e.target.value)} placeholder="Acme Bank" required /></Field>
        <Field label="Admin email" hint="The first Company Admin account for this company.">
          <input className="av-input" type="email" value={form.admin_email} onChange={(e) => set('admin_email', e.target.value)} placeholder="admin@acme.com" required />
        </Field>
        <Field label="Admin name"><input className="av-input" value={form.admin_name} onChange={(e) => set('admin_name', e.target.value)} placeholder="Priya Sharma" /></Field>
        <Field label="Seat limit" hint="Maximum active sales agents.">
          <input className="av-input" type="number" min={1} value={form.max_seats} onChange={(e) => set('max_seats', e.target.value)} />
        </Field>
      </form>
    </Modal>
  );
}

// ── Company users drawer ─────────────────────────────────────────────────────────
function CompanyUsersModal({ company, onClose, showToast }) {
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [adding, setAdding] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    platform.companyUsers(company.id, { page_size: 100 })
      .then((d) => setUsers(d.items)).catch((e) => showToast(e.message, 'error'))
      .finally(() => setLoading(false));
  }, [company.id, showToast]);
  useEffect(() => { load(); }, [load]);

  return (
    <Modal title={`${company.name} · users`} onClose={onClose} width={620}
      footer={<Button variant="secondary" onClick={onClose}>Close</Button>}>
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 12 }}>
        <Button variant="secondary" onClick={() => setAdding(true)}><UserPlus size={15} /> Add admin</Button>
      </div>
      <DataTable
        columns={[
          { key: 'name', label: 'Name', render: (r) => <div><div className="av-cell-strong">{r.name}</div><div className="av-cell-sub">{r.email}</div></div> },
          { key: 'role', label: 'Role', render: (r) => <span className="av-mono">{r.role}</span> },
          { key: 'status', label: 'Status', render: (r) => <Badge status={r.status} /> },
        ]}
        rows={users} loading={loading} empty="No users."
      />
      {adding && <AddAdminModal company={company} onClose={() => setAdding(false)} onAdded={load} showToast={showToast} />}
    </Modal>
  );
}

function AddAdminModal({ company, onClose, onAdded, showToast }) {
  const [form, setForm] = useState({ email: '', name: '' });
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const submit = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      const d = await platform.createAdmin(company.id, form);
      setResult(d.user); onAdded();
    } catch (err) { showToast(err.message, 'error'); } finally { setLoading(false); }
  };
  if (result) {
    return (
      <Modal title="Admin added" onClose={onClose} footer={<Button onClick={onClose}>Done</Button>}>
        <CredsBox email={result.email} password={result.temp_password} />
      </Modal>
    );
  }
  return (
    <Modal title="Add company admin" onClose={onClose}
      footer={<><Button variant="secondary" onClick={onClose}>Cancel</Button><Button onClick={submit} loading={loading}>Add admin</Button></>}>
      <form onSubmit={submit}>
        <Field label="Email"><input className="av-input" type="email" value={form.email} onChange={(e) => setForm((f) => ({ ...f, email: e.target.value }))} required /></Field>
        <Field label="Name"><input className="av-input" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} /></Field>
      </form>
    </Modal>
  );
}

// ── Companies page ────────────────────────────────────────────────────────────
function CompaniesPage({ showToast }) {
  const [data, setData] = useState({ items: [], total: 0, page: 1, page_size: 20 });
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState('');
  const [page, setPage] = useState(1);
  const [creating, setCreating] = useState(false);
  const [viewing, setViewing] = useState(null);

  const load = useCallback(() => {
    setLoading(true);
    platform.companies({ q, page, page_size: 20 })
      .then(setData).catch((e) => showToast(e.message, 'error')).finally(() => setLoading(false));
  }, [q, page, showToast]);
  useEffect(() => { const t = setTimeout(load, q ? 250 : 0); return () => clearTimeout(t); }, [load, q]);

  const toggle = async (c) => {
    try {
      await (c.status === 'active' ? platform.disableCompany(c.id) : platform.enableCompany(c.id));
      showToast(`${c.name} ${c.status === 'active' ? 'disabled' : 'enabled'}`); load();
    } catch (e) { showToast(e.message, 'error'); }
  };
  const remove = async (c) => {
    if (!window.confirm(`Delete "${c.name}"? This removes all its users and data.`)) return;
    try { await platform.deleteCompany(c.id); showToast('Company deleted'); load(); }
    catch (e) { showToast(e.message, 'error'); }
  };

  return (
    <div className="av-page">
      <div className="av-page-head">
        <div><div className="av-page-title">Companies</div>
          <div className="av-page-sub">Create and manage organizations on the platform</div></div>
        <div className="av-toolbar">
          <SearchBar value={q} onChange={(v) => { setPage(1); setQ(v); }} placeholder="Search companies…" />
          <Button onClick={() => setCreating(true)}><Plus size={16} /> New company</Button>
        </div>
      </div>
      <div className="av-card">
        <DataTable
          columns={[
            { key: 'name', label: 'Company', render: (r) => <div><div className="av-cell-strong">{r.name}</div><div className="av-cell-sub">{r.slug}</div></div> },
            { key: 'agent_count', label: 'Agents', render: (r) => `${r.agent_count ?? 0} / ${r.max_seats}` },
            { key: 'admin_count', label: 'Admins', render: (r) => r.admin_count ?? 0 },
            { key: 'status', label: 'Status', render: (r) => <Badge status={r.status} /> },
            { key: 'created_at', label: 'Created', render: (r) => fmtDate(r.created_at) },
            { key: 'actions', label: '', render: (r) => (
              <div className="av-row-actions">
                <button className="av-icon-btn" title="View users" onClick={() => setViewing(r)}><Eye size={15} /></button>
                <button className="av-icon-btn" title={r.status === 'active' ? 'Disable' : 'Enable'} onClick={() => toggle(r)}><Power size={15} /></button>
                <button className="av-icon-btn danger" title="Delete" onClick={() => remove(r)}><Trash2 size={15} /></button>
              </div>
            ) },
          ]}
          rows={data.items} loading={loading} empty="No companies yet — create the first one."
        />
        <Pagination page={data.page} pageSize={data.page_size} total={data.total} onPage={setPage} />
      </div>
      {creating && <CreateCompanyModal onClose={() => setCreating(false)} onCreated={load} showToast={showToast} />}
      {viewing && <CompanyUsersModal company={viewing} onClose={() => setViewing(null)} showToast={showToast} />}
    </div>
  );
}

// ── Audit page ────────────────────────────────────────────────────────────────
function AuditPage({ showToast }) {
  const [data, setData] = useState({ items: [], total: 0, page: 1, page_size: 50 });
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const load = useCallback(() => {
    setLoading(true);
    platform.audit({ page, page_size: 50 }).then(setData)
      .catch((e) => showToast(e.message, 'error')).finally(() => setLoading(false));
  }, [page, showToast]);
  useEffect(() => { load(); }, [load]);
  return (
    <div className="av-page">
      <div className="av-page-head">
        <div><div className="av-page-title">Audit log</div>
          <div className="av-page-sub">Every administrative action across the platform</div></div>
        <Button variant="secondary" onClick={load}><RefreshCw size={15} /> Refresh</Button>
      </div>
      <div className="av-card">
        <DataTable
          columns={[
            { key: 'created_at', label: 'When', render: (r) => r.created_at ? new Date(r.created_at).toLocaleString('en-IN') : '—' },
            { key: 'action', label: 'Action', render: (r) => <span className="av-audit-action">{r.action}</span> },
            { key: 'entity', label: 'Entity', render: (r) => <span className="av-cell-sub">{r.entity_type || '—'}</span> },
            { key: 'meta', label: 'Details', render: (r) => <span className="av-cell-sub">{JSON.stringify(r.meta || {})}</span> },
          ]}
          rows={data.items} loading={loading} empty="No audit entries."
        />
        <Pagination page={data.page} pageSize={data.page_size} total={data.total} onPage={setPage} />
      </div>
    </div>
  );
}

// ── Platform analytics (cross-company) ───────────────────────────────────────
function PlatformAnalytics({ showToast }) {
  const [data, setData] = useState(null);
  const [days, setDays] = useState(14);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    platform.analytics(days).then((d) => setData(d.analytics))
      .catch((e) => showToast(e.message, 'error')).finally(() => setLoading(false));
  }, [days, showToast]);
  useEffect(() => { load(); }, [load]);

  const t = data?.totals || {};
  const maxAct = Math.max(1, ...(data?.companies || []).map((c) => c.pitches + c.chats + (c.roleplays || 0)));

  return (
    <div className="av-page">
      <div className="av-page-head">
        <div><div className="av-page-title">Platform analytics</div>
          <div className="av-page-sub">Usage across all companies</div></div>
        <select className="av-select" style={{ width: 'auto' }} value={days} onChange={(e) => setDays(Number(e.target.value))}>
          <option value={7}>Last 7 days</option>
          <option value={14}>Last 14 days</option>
          <option value={30}>Last 30 days</option>
        </select>
      </div>
      <div className="av-stats-grid">
        <StatCard icon={Building2} label="Companies" value={loading ? '—' : t.companies} />
        <StatCard icon={Users} label="Sales agents" value={loading ? '—' : t.agents} />
        <StatCard icon={Sparkles} label="Pitches" value={loading ? '—' : t.pitches} />
        <StatCard icon={MessageSquare} label="Chats" value={loading ? '—' : t.chats} />
        <StatCard icon={Headphones} label="Roleplays" value={loading ? '—' : (t.roleplays ?? 0)} />
        <StatCard icon={Award} label="Avg. score" value={loading || t.avg_score == null ? '—' : `${t.avg_score}/100`} />
      </div>
      <div className="av-card av-section">
        <div className="av-card-head"><span className="av-card-title">Activity · last {days} days</span></div>
        <div style={{ padding: '14px 18px 18px' }}>
          {loading || !data ? <div className="av-table-msg"><Spinner /> Loading…</div> : <ActivityChart data={data.timeseries} />}
        </div>
      </div>
      <div className="av-card">
        <div className="av-card-head"><span className="av-card-title">Per-company activity</span></div>
        <DataTable
          columns={[
            { key: 'name', label: 'Company', render: (r) => <div><div className="av-cell-strong">{r.name}</div><div className="av-cell-sub">{r.slug}</div></div> },
            { key: 'agents', label: 'Agents', render: (r) => r.agents },
            { key: 'pitches', label: 'Pitches', render: (r) => r.pitches },
            { key: 'chats', label: 'Chats', render: (r) => r.chats },
            { key: 'roleplays', label: 'Roleplays', render: (r) => r.roleplays ?? 0 },
            { key: 'activity', label: 'Activity', render: (r) => <MiniBar value={r.pitches + r.chats + (r.roleplays || 0)} max={maxAct} /> },
            { key: 'status', label: 'Status', render: (r) => <Badge status={r.status} /> },
          ]}
          rows={data?.companies || []} loading={loading} empty="No companies yet."
        />
      </div>
    </div>
  );
}

export default function SuperAdminConsole({ page, showToast }) {
  if (page === 'analytics') return <PlatformAnalytics showToast={showToast} />;
  if (page === 'companies') return <CompaniesPage showToast={showToast} />;
  if (page === 'logins') return <LoginHistoryPage fetcher={platform.loginHistory} showToast={showToast} />;
  if (page === 'audit') return <AuditPage showToast={showToast} />;
  return <Overview />;
}
