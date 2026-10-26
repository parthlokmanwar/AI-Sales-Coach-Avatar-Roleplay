import { useEffect, useState, useCallback } from 'react';
import {
  X, Search, ChevronLeft, ChevronRight, Check, Copy, AlertTriangle, CheckCircle2,
} from 'lucide-react';

// ── Toast ─────────────────────────────────────────────────────────────────────
export function useToast() {
  const [toast, setToast] = useState(null);
  const show = useCallback((msg, type = 'success') => {
    setToast({ msg, type });
    setTimeout(() => setToast(null), 3500);
  }, []);
  const node = toast ? (
    <div className={`av-toast ${toast.type}`}>
      {toast.type === 'success' ? <CheckCircle2 size={16} /> : <AlertTriangle size={16} />}
      {toast.msg}
    </div>
  ) : null;
  return { show, node };
}

// ── Spinner ─────────────────────────────────────────────────────────────────
export function Spinner({ size = 18 }) {
  return <span className="av-spinner" style={{ width: size, height: size }} />;
}

// ── Stat card ─────────────────────────────────────────────────────────────────
export function StatCard({ icon: Icon, label, value, accent }) {
  return (
    <div className="av-stat">
      <div className="av-stat-ic" style={accent ? { background: accent } : undefined}>
        {Icon && <Icon size={18} />}
      </div>
      <div className="av-stat-body">
        <div className="av-stat-value">{value}</div>
        <div className="av-stat-label">{label}</div>
      </div>
    </div>
  );
}

// ── Status badge ──────────────────────────────────────────────────────────────
export function Badge({ status }) {
  const ok = status === 'active';
  const cls = ok ? 'ok' : (status === 'invited' ? 'warn' : 'off');
  return <span className={`av-badge ${cls}`}><span className="dot" />{status}</span>;
}

// ── Search bar ────────────────────────────────────────────────────────────────
export function SearchBar({ value, onChange, placeholder = 'Search…' }) {
  return (
    <div className="av-search">
      <Search size={15} />
      <input value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} />
    </div>
  );
}

// ── Data table ────────────────────────────────────────────────────────────────
// columns: [{ key, label, render?(row), width? }]
export function DataTable({ columns, rows, loading, empty = 'No records found.', keyField = 'id' }) {
  return (
    <div className="av-table-wrap">
      <table className="av-table">
        <thead>
          <tr>{columns.map((c) => <th key={c.key} style={c.width ? { width: c.width } : undefined}>{c.label}</th>)}</tr>
        </thead>
        <tbody>
          {loading ? (
            <tr><td colSpan={columns.length} className="av-table-msg"><Spinner /> Loading…</td></tr>
          ) : rows.length === 0 ? (
            <tr><td colSpan={columns.length} className="av-table-msg">{empty}</td></tr>
          ) : (
            rows.map((row) => (
              <tr key={row[keyField]}>
                {columns.map((c) => <td key={c.key}>{c.render ? c.render(row) : row[c.key]}</td>)}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}

// ── Pagination ────────────────────────────────────────────────────────────────
export function Pagination({ page, pageSize, total, onPage }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (pages <= 1) return null;
  return (
    <div className="av-pager">
      <span>{(page - 1) * pageSize + 1}–{Math.min(page * pageSize, total)} of {total}</span>
      <button disabled={page <= 1} onClick={() => onPage(page - 1)}><ChevronLeft size={16} /></button>
      <button disabled={page >= pages} onClick={() => onPage(page + 1)}><ChevronRight size={16} /></button>
    </div>
  );
}

// ── Activity chart (stacked bars: pitches + chats per day) ─────────────────────
export function ActivityChart({ data }) {
  const max = Math.max(1, ...data.map((d) => d.pitches + d.chats));
  return (
    <div>
      <div className="av-chart">
        {data.map((d, i) => (
          <div key={i} className="av-chart-col" title={`${d.label}: ${d.pitches} pitches, ${d.chats} chats`}>
            <div className="av-chart-bar">
              <div className="seg chats" style={{ height: `${(d.chats / max) * 100}%` }} />
              <div className="seg pitches" style={{ height: `${(d.pitches / max) * 100}%` }} />
            </div>
            <div className="av-chart-x">{d.label.split(' ')[0]}</div>
          </div>
        ))}
      </div>
      <div className="av-legend">
        <span><i className="dot pitches" /> Pitches</span>
        <span><i className="dot chats" /> Chats</span>
      </div>
    </div>
  );
}

// ── Mini inline bar (for table cells) ──────────────────────────────────────────
export function MiniBar({ value, max }) {
  const pct = max > 0 ? Math.round((value / max) * 100) : 0;
  return (
    <div className="av-minibar"><div className="av-minibar-fill" style={{ width: `${pct}%` }} /></div>
  );
}

// ── Modal ─────────────────────────────────────────────────────────────────────
export function Modal({ title, onClose, children, footer, width = 460 }) {
  useEffect(() => {
    const h = (e) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [onClose]);
  return (
    <div className="av-modal-overlay" onClick={onClose}>
      <div className="av-modal" style={{ maxWidth: width }} onClick={(e) => e.stopPropagation()}>
        <div className="av-modal-head">
          <h3>{title}</h3>
          <button className="av-icon-btn" onClick={onClose}><X size={18} /></button>
        </div>
        <div className="av-modal-body">{children}</div>
        {footer && <div className="av-modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

// ── Buttons ───────────────────────────────────────────────────────────────────
export function Button({ children, variant = 'primary', loading, ...rest }) {
  return (
    <button className={`av-btn ${variant}`} disabled={loading || rest.disabled} {...rest}>
      {loading ? <Spinner size={15} /> : children}
    </button>
  );
}

// ── Field ─────────────────────────────────────────────────────────────────────
export function Field({ label, children, hint }) {
  return (
    <label className="av-field">
      <span className="av-field-label">{label}</span>
      {children}
      {hint && <span className="av-field-hint">{hint}</span>}
    </label>
  );
}

// ── Copyable credential row (shown once after create/reset) ────────────────────
export function CredsBox({ email, password }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard.writeText(`${email} / ${password}`);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return (
    <div className="av-creds">
      <div>
        <div className="av-creds-row"><span>Email</span><code>{email}</code></div>
        <div className="av-creds-row"><span>Temp password</span><code>{password}</code></div>
      </div>
      <button className="av-btn ghost" onClick={copy}>
        {copied ? <><Check size={14} /> Copied</> : <><Copy size={14} /> Copy</>}
      </button>
    </div>
  );
}
