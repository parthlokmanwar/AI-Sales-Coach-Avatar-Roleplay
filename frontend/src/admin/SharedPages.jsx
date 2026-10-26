import { useState, useEffect, useCallback } from 'react';
import { RefreshCw } from 'lucide-react';
import { DataTable, Pagination, Button } from './ui';

const fmtTime = (iso) => (iso ? new Date(iso).toLocaleString('en-IN') : '—');

// Reusable across the Super Admin (platform) and Company Admin consoles — the
// caller passes the appropriate `fetcher` (platform.loginHistory / admin.loginHistory).
export function LoginHistoryPage({ fetcher, showToast }) {
  const [data, setData] = useState({ items: [], total: 0, page: 1, page_size: 50 });
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);

  const load = useCallback(() => {
    setLoading(true);
    fetcher({ page, page_size: 50 }).then(setData)
      .catch((e) => showToast(e.message, 'error')).finally(() => setLoading(false));
  }, [page, fetcher, showToast]);
  useEffect(() => { load(); }, [load]);

  return (
    <div className="av-page">
      <div className="av-page-head">
        <div><div className="av-page-title">Sign-in history</div>
          <div className="av-page-sub">Recent login attempts (successful and failed)</div></div>
        <Button variant="secondary" onClick={load}><RefreshCw size={15} /> Refresh</Button>
      </div>
      <div className="av-card">
        <DataTable
          columns={[
            { key: 'created_at', label: 'When', render: (r) => fmtTime(r.created_at) },
            { key: 'email', label: 'Email', render: (r) => <span className="av-cell-strong">{r.email}</span> },
            { key: 'success', label: 'Result', render: (r) => (
              <span className={`av-badge ${r.success ? 'ok' : 'off'}`}>
                <span className="dot" />{r.success ? 'success' : 'failed'}
              </span>
            ) },
            { key: 'reason', label: 'Reason', render: (r) => <span className="av-cell-sub">{r.reason || '—'}</span> },
            { key: 'ip', label: 'IP', render: (r) => <span className="av-mono">{r.ip || '—'}</span> },
          ]}
          rows={data.items} loading={loading} empty="No sign-in records yet."
        />
        <Pagination page={data.page} pageSize={data.page_size} total={data.total} onPage={setPage} />
      </div>
    </div>
  );
}
