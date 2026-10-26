import { useState, useEffect, useCallback, useRef } from 'react';
import {
  Users, UserCheck, Layers, Plus, Power, KeyRound, FileText, Upload, Trash2, RefreshCw, File,
  Sparkles, MessageSquare, Download, FileSpreadsheet, Headphones, Award,
} from 'lucide-react';
import { admin, downloadFile } from './api';
import {
  StatCard, Badge, DataTable, SearchBar, Pagination, Modal, Button, Field, CredsBox, Spinner,
  ActivityChart, MiniBar,
} from './ui';
import { LoginHistoryPage } from './SharedPages';
import SessionsPage from './SessionsPage';

const fmtDate = (iso) => (iso ? new Date(iso).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' }) : '—');

// ── Overview ──────────────────────────────────────────────────────────────────
function Overview() {
  const [stats, setStats] = useState(null);
  useEffect(() => { admin.stats().then((d) => setStats(d.stats)).catch(() => {}); }, []);
  return (
    <div className="av-page">
      <div className="av-page-head">
        <div><div className="av-page-title">{stats?.company?.name || 'Company'} overview</div>
          <div className="av-page-sub">Your team's activity and capacity</div></div>
      </div>
      <div className="av-stats-grid">
        <StatCard icon={Users} label="Sales agents" value={stats ? stats.agents : '—'} />
        <StatCard icon={UserCheck} label="Active agents" value={stats ? stats.active_agents : '—'} />
        <StatCard icon={Layers} label="Seats used" value={stats ? `${stats.seats_used} / ${stats.max_seats}` : '—'} />
      </div>
    </div>
  );
}

// ── Create agent ──────────────────────────────────────────────────────────────
function CreateAgentModal({ onClose, onCreated, showToast }) {
  const [form, setForm] = useState({ email: '', name: '' });
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const submit = async (e) => {
    e.preventDefault();
    setLoading(true);
    try { const d = await admin.createAgent(form); setResult(d.user); onCreated(); }
    catch (err) { showToast(err.message, 'error'); } finally { setLoading(false); }
  };
  if (result) {
    return (
      <Modal title="Agent created" onClose={onClose} footer={<Button onClick={onClose}>Done</Button>}>
        <p style={{ fontSize: 13, color: 'var(--av-muted)', marginBottom: 14 }}>
          Share these credentials with the agent. The password is shown once and must be reset on first sign-in.
        </p>
        <CredsBox email={result.email} password={result.temp_password} />
      </Modal>
    );
  }
  return (
    <Modal title="New sales agent" onClose={onClose}
      footer={<><Button variant="secondary" onClick={onClose}>Cancel</Button><Button onClick={submit} loading={loading}>Create agent</Button></>}>
      <form onSubmit={submit}>
        <Field label="Email"><input className="av-input" type="email" value={form.email} onChange={(e) => setForm((f) => ({ ...f, email: e.target.value }))} placeholder="agent@company.com" required /></Field>
        <Field label="Name"><input className="av-input" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} placeholder="Full name" /></Field>
      </form>
    </Modal>
  );
}

// ── Agents page ───────────────────────────────────────────────────────────────
function AgentsPage({ showToast }) {
  const [data, setData] = useState({ items: [], total: 0, page: 1, page_size: 20 });
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState('');
  const [page, setPage] = useState(1);
  const [creating, setCreating] = useState(false);
  const [creds, setCreds] = useState(null);

  const load = useCallback(() => {
    setLoading(true);
    admin.agents({ q, page, page_size: 20 }).then(setData)
      .catch((e) => showToast(e.message, 'error')).finally(() => setLoading(false));
  }, [q, page, showToast]);
  useEffect(() => { const t = setTimeout(load, q ? 250 : 0); return () => clearTimeout(t); }, [load, q]);

  const toggle = async (a) => {
    try {
      await (a.status === 'active' ? admin.deactivate(a.id) : admin.activate(a.id));
      showToast(`${a.name} ${a.status === 'active' ? 'deactivated' : 'activated'}`); load();
    } catch (e) { showToast(e.message, 'error'); }
  };
  const reset = async (a) => {
    try { const d = await admin.resetPassword(a.id); setCreds(d.user); }
    catch (e) { showToast(e.message, 'error'); }
  };

  return (
    <div className="av-page">
      <div className="av-page-head">
        <div><div className="av-page-title">Sales agents</div>
          <div className="av-page-sub">Create accounts and manage access for your team</div></div>
        <div className="av-toolbar">
          <SearchBar value={q} onChange={(v) => { setPage(1); setQ(v); }} placeholder="Search agents…" />
          <Button onClick={() => setCreating(true)}><Plus size={16} /> Add agent</Button>
        </div>
      </div>
      <div className="av-card">
        <DataTable
          columns={[
            { key: 'name', label: 'Agent', render: (r) => <div><div className="av-cell-strong">{r.name}</div><div className="av-cell-sub">{r.email}</div></div> },
            { key: 'status', label: 'Status', render: (r) => <Badge status={r.status} /> },
            { key: 'last_login_at', label: 'Last sign-in', render: (r) => fmtDate(r.last_login_at) },
            { key: 'created_at', label: 'Added', render: (r) => fmtDate(r.created_at) },
            { key: 'actions', label: '', render: (r) => (
              <div className="av-row-actions">
                <button className="av-icon-btn" title="Reset password" onClick={() => reset(r)}><KeyRound size={15} /></button>
                <button className="av-icon-btn" title={r.status === 'active' ? 'Deactivate' : 'Activate'} onClick={() => toggle(r)}><Power size={15} /></button>
              </div>
            ) },
          ]}
          rows={data.items} loading={loading} empty="No agents yet — add your first team member."
        />
        <Pagination page={data.page} pageSize={data.page_size} total={data.total} onPage={setPage} />
      </div>
      {creating && <CreateAgentModal onClose={() => setCreating(false)} onCreated={load} showToast={showToast} />}
      {creds && (
        <Modal title="New password generated" onClose={() => setCreds(null)} footer={<Button onClick={() => setCreds(null)}>Done</Button>}>
          <CredsBox email={creds.email} password={creds.temp_password} />
        </Modal>
      )}
    </div>
  );
}

// ── Documents page ────────────────────────────────────────────────────────────
function DocumentsPage({ showToast }) {
  const [files, setFiles] = useState([]);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [drag, setDrag] = useState(false);
  const inputRef = useRef(null);

  const load = useCallback(() => {
    setLoading(true);
    admin.documents().then((d) => setFiles(d.files)).catch((e) => showToast(e.message, 'error')).finally(() => setLoading(false));
  }, [showToast]);
  useEffect(() => { load(); }, [load]);

  const upload = async (file) => {
    if (!file) return;
    setUploading(true);
    try {
      const fd = new FormData(); fd.append('file', file);
      const d = await admin.uploadDocument(fd);
      setFiles(d.files); showToast(`Uploaded ${file.name}`);
    } catch (e) { showToast(e.message, 'error'); } finally { setUploading(false); }
  };
  const remove = async (name) => {
    if (!window.confirm(`Delete "${name}" from your company's library?`)) return;
    try { const d = await admin.deleteDocument(name); setFiles(d.files); showToast('Document deleted'); }
    catch (e) { showToast(e.message, 'error'); }
  };

  return (
    <div className="av-page">
      <div className="av-page-head">
        <div><div className="av-page-title">Document library</div>
          <div className="av-page-sub">These documents ground your team's pitch &amp; chat answers</div></div>
      </div>
      <div className="av-section">
        <div className={`av-drop ${drag ? 'drag' : ''}`}
          onClick={() => inputRef.current?.click()}
          onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => { e.preventDefault(); setDrag(false); upload(e.dataTransfer.files?.[0]); }}>
          {uploading ? <><Spinner /> Uploading…</> : <>
            <Upload size={22} style={{ marginBottom: 8 }} /><br />
            <strong>Click to upload</strong> or drag a file here<br />
            <span style={{ fontSize: 12 }}>PDF, DOCX, PPTX, XLSX, CSV, TXT</span>
          </>}
          <input ref={inputRef} type="file" hidden accept=".pdf,.docx,.pptx,.xlsx,.xls,.csv,.txt"
            onChange={(e) => { upload(e.target.files?.[0]); e.target.value = ''; }} />
        </div>
      </div>
      <div className="av-card">
        <div className="av-card-head"><span className="av-card-title">Documents ({files.length})</span></div>
        <DataTable
          keyField="name"
          columns={[
            { key: 'name', label: 'Document', render: (r) => <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}><File size={15} style={{ color: 'var(--av-faint)' }} /><span className="av-cell-strong">{r.name}</span></div> },
            { key: 'type', label: 'Type', render: (r) => <span className="av-cell-sub">{r.format || r.type || '—'}</span> },
            { key: 'chunks', label: 'Chunks', render: (r) => r.chunks ?? r.rows ?? '—' },
            { key: 'actions', label: '', render: (r) => (
              <div className="av-row-actions">
                <button className="av-icon-btn danger" title="Delete" onClick={() => remove(r.name)}><Trash2 size={15} /></button>
              </div>
            ) },
          ]}
          rows={files} loading={loading} empty="No documents yet — upload your first file above."
        />
      </div>
    </div>
  );
}

// ── Analytics page ──────────────────────────────────────────────────────────────
function AnalyticsPage({ showToast }) {
  const [data, setData] = useState(null);
  const [days, setDays] = useState(14);
  const [loading, setLoading] = useState(true);
  const [exporting, setExporting] = useState('');

  const load = useCallback(() => {
    setLoading(true);
    admin.analytics(days).then((d) => setData(d.analytics))
      .catch((e) => showToast(e.message, 'error')).finally(() => setLoading(false));
  }, [days, showToast]);
  useEffect(() => { load(); }, [load]);

  const exportFile = async (fmt) => {
    setExporting(fmt);
    try { await downloadFile(admin.exportUrl(fmt), `agents.${fmt}`); showToast(`Exported agents.${fmt}`); }
    catch (e) { showToast(e.message, 'error'); } finally { setExporting(''); }
  };

  const t = data?.totals || {};
  const maxActivity = Math.max(1, ...(data?.agents || []).map((a) => a.pitches + a.chats + (a.roleplays || 0)));

  return (
    <div className="av-page">
      <div className="av-page-head">
        <div><div className="av-page-title">Analytics</div>
          <div className="av-page-sub">Team performance &amp; activity</div></div>
        <div className="av-toolbar">
          <select className="av-select" style={{ width: 'auto' }} value={days} onChange={(e) => setDays(Number(e.target.value))}>
            <option value={7}>Last 7 days</option>
            <option value={14}>Last 14 days</option>
            <option value={30}>Last 30 days</option>
          </select>
          <Button variant="secondary" onClick={() => exportFile('csv')} loading={exporting === 'csv'}><Download size={15} /> CSV</Button>
          <Button variant="secondary" onClick={() => exportFile('xlsx')} loading={exporting === 'xlsx'}><FileSpreadsheet size={15} /> Excel</Button>
          <Button variant="secondary" onClick={() => exportFile('pdf')} loading={exporting === 'pdf'}><FileText size={15} /> PDF</Button>
        </div>
      </div>

      <div className="av-stats-grid">
        <StatCard icon={Users} label="Agents" value={loading ? '—' : t.agents} />
        <StatCard icon={Sparkles} label="Pitches" value={loading ? '—' : t.pitches} />
        <StatCard icon={MessageSquare} label="Chats" value={loading ? '—' : t.chats} />
        <StatCard icon={Headphones} label="Roleplays" value={loading ? '—' : (t.roleplays ?? 0)} />
        <StatCard icon={Award} label="Avg. roleplay score" value={loading || t.avg_score == null ? '—' : `${t.avg_score}/100`} />
      </div>

      <div className="av-card av-section">
        <div className="av-card-head"><span className="av-card-title">Activity · last {days} days</span></div>
        <div style={{ padding: '14px 18px 18px' }}>
          {loading || !data ? <div className="av-table-msg"><Spinner /> Loading…</div>
            : <ActivityChart data={data.timeseries} />}
        </div>
      </div>

      <div className="av-card">
        <div className="av-card-head"><span className="av-card-title">Per-agent activity</span></div>
        <DataTable
          columns={[
            { key: 'name', label: 'Agent', render: (r) => <div><div className="av-cell-strong">{r.name}</div><div className="av-cell-sub">{r.email}</div></div> },
            { key: 'pitches', label: 'Pitches', render: (r) => r.pitches },
            { key: 'chats', label: 'Chats', render: (r) => r.chats },
            { key: 'roleplays', label: 'Roleplays', render: (r) => r.roleplays ?? 0 },
            { key: 'avg_score', label: 'Avg score', render: (r) => (r.avg_score != null ? `${r.avg_score}/100` : '—') },
            { key: 'activity', label: 'Activity', render: (r) => <MiniBar value={r.pitches + r.chats + (r.roleplays || 0)} max={maxActivity} /> },
            { key: 'status', label: 'Status', render: (r) => <Badge status={r.status} /> },
            { key: 'last_login_at', label: 'Last sign-in', render: (r) => fmtDate(r.last_login_at) },
          ]}
          rows={data?.agents || []} loading={loading} empty="No agent activity yet."
        />
      </div>
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
    admin.audit({ page, page_size: 50 }).then(setData)
      .catch((e) => showToast(e.message, 'error')).finally(() => setLoading(false));
  }, [page, showToast]);
  useEffect(() => { load(); }, [load]);
  return (
    <div className="av-page">
      <div className="av-page-head">
        <div><div className="av-page-title">Audit log</div>
          <div className="av-page-sub">Administrative actions in your company</div></div>
        <Button variant="secondary" onClick={load}><RefreshCw size={15} /> Refresh</Button>
      </div>
      <div className="av-card">
        <DataTable
          columns={[
            { key: 'created_at', label: 'When', render: (r) => r.created_at ? new Date(r.created_at).toLocaleString('en-IN') : '—' },
            { key: 'action', label: 'Action', render: (r) => <span className="av-audit-action">{r.action}</span> },
            { key: 'entity', label: 'Entity', render: (r) => <span className="av-cell-sub">{r.entity_type || '—'}</span> },
          ]}
          rows={data.items} loading={loading} empty="No audit entries."
        />
        <Pagination page={data.page} pageSize={data.page_size} total={data.total} onPage={setPage} />
      </div>
    </div>
  );
}

export default function CompanyAdminConsole({ page, showToast }) {
  if (page === 'analytics') return <AnalyticsPage showToast={showToast} />;
  if (page === 'sessions') return <SessionsPage showToast={showToast} />;
  if (page === 'agents') return <AgentsPage showToast={showToast} />;
  if (page === 'documents') return <DocumentsPage showToast={showToast} />;
  if (page === 'logins') return <LoginHistoryPage fetcher={admin.loginHistory} showToast={showToast} />;
  if (page === 'audit') return <AuditPage showToast={showToast} />;
  return <Overview />;
}
