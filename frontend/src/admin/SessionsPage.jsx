// Sessions — the admin side of the slot-booking system (Assistant mode).
//
// Create a day of 1-hour slots (10:00, 11:00, …) with a pinned scenario and a
// seat capacity, watch the roster fill live, assign agents directly, and open
// the session report once the hour completes: attendance, score spread, weak
// areas, and drill-down into each agent's full graded report.

import { useState, useEffect, useCallback } from 'react';
import {
  Calendar, Plus, Users, XCircle, ClipboardList, BarChart3, UserPlus, Trash2, RefreshCw,
} from 'lucide-react';
import { admin, sessions } from './api';
import { Modal, Button, Field, Spinner } from './ui';
import { PRESETS, EXISTING_PRESETS, ETB_SERVICE_PRESETS } from '../components/RolePlay/presets';
import { ROLEPLAY_MODEL } from '../components/RolePlay/avatarMap';

const HOURS = [9, 10, 11, 12, 13, 14, 15, 16, 17, 18];
const fmtHour = (h) => `${String(h % 12 === 0 ? 12 : h % 12).padStart(2, '0')}:00 ${h < 12 ? 'AM' : 'PM'}`;
const fmtTime = (iso) => new Date(iso).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' });
const fmtDay = (iso) => new Date(iso).toLocaleDateString('en-IN', { weekday: 'short', day: '2-digit', month: 'short' });
const todayStr = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};

// Scenario catalog = the same preset cards the Practice gallery shows, so an
// admin-planned session and a self-practice run are the same simulation. The
// snapshot mirrors PresetGallery.handleJoin exactly; it's stored on the slot
// so later preset edits never rewrite a scheduled session.
const CATALOG = [
  ...PRESETS.map((p) => ({ ...p, group: 'New customer (NTB)' })),
  ...EXISTING_PRESETS.map((p) => ({ ...p, group: 'Existing customer' })),
  ...ETB_SERVICE_PRESETS.map((p) => ({ ...p, group: 'ETB service call' })),
];

function snapshotFor(preset, language) {
  return {
    ...preset.form,
    difficulty: preset.difficulty,
    customer_type: preset.customer_type || 'new',
    replica_id: preset.replica_id,
    persona_id: '',                 // always a fresh persona per booked call
    model: ROLEPLAY_MODEL,
    language,
  };
}

// ── Create-session modal ─────────────────────────────────────────────────────
function CreateModal({ onClose, onCreated, showToast }) {
  const [date, setDate] = useState(todayStr());
  const [hours, setHours] = useState([10, 11, 12]);
  const [kind, setKind] = useState('assistant');
  const [presetId, setPresetId] = useState(CATALOG[0]?.id || '');
  const [language, setLanguage] = useState('english');
  const [capacity, setCapacity] = useState(25);
  const [enrollment, setEnrollment] = useState('open');
  const [title, setTitle] = useState('');
  const [loading, setLoading] = useState(false);

  const toggleHour = (h) =>
    setHours((prev) => (prev.includes(h) ? prev.filter((x) => x !== h) : [...prev, h].sort((a, b) => a - b)));

  const submit = async () => {
    if (!hours.length) { showToast('Pick at least one time slot.', 'error'); return; }
    const preset = CATALOG.find((p) => p.id === presetId);
    if (kind === 'assistant' && !preset) { showToast('Pick a scenario.', 'error'); return; }
    setLoading(true);
    try {
      const slots = hours.map((h) => {
        const start = new Date(`${date}T${String(h).padStart(2, '0')}:00:00`);
        return { start_at: start.toISOString(), end_at: new Date(start.getTime() + 3600e3).toISOString() };
      });
      const d = await sessions.create({
        kind, slots, capacity: Number(capacity) || 25, enrollment,
        title: title || (kind === 'assistant' ? (preset ? `${preset.name} — ${preset.tag}` : '') : 'Practice hour'),
        scenario_ref: kind === 'assistant' ? presetId : '',
        scenario_config: kind === 'assistant' ? snapshotFor(preset, language) : {},
      });
      showToast(`${d.created.length} slot${d.created.length === 1 ? '' : 's'} created${d.created.length < hours.length ? ' (duplicates skipped)' : ''}.`);
      onCreated();
      onClose();
    } catch (e) {
      showToast(e.message, 'error');
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal title="New session" onClose={onClose} width={560}
      footer={<><Button variant="secondary" onClick={onClose}>Cancel</Button>
        <Button onClick={submit} loading={loading}>Create {hours.length || ''} slot{hours.length === 1 ? '' : 's'}</Button></>}>
      <Field label="Date">
        <input className="av-input" type="date" value={date} min={todayStr()}
          onChange={(e) => setDate(e.target.value)} />
      </Field>
      <Field label="Time slots (1 hour each)" hint="Every selected hour becomes its own bookable slot.">
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
          {HOURS.map((h) => (
            <button key={h} type="button" onClick={() => toggleHour(h)}
              className={`av-btn ${hours.includes(h) ? 'primary' : 'ghost'}`}
              style={{ padding: '6px 10px', fontSize: 12 }}>
              {fmtHour(h)}
            </button>
          ))}
        </div>
      </Field>
      <Field label="Mode">
        <select className="av-input" value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="assistant">Assistant — scenario pinned by you</option>
          <option value="practice">Practice — agent picks their own scenario</option>
        </select>
      </Field>
      {kind === 'assistant' && (
        <>
          <Field label="Scenario">
            <select className="av-input" value={presetId} onChange={(e) => setPresetId(e.target.value)}>
              {['New customer (NTB)', 'Existing customer', 'ETB service call'].map((g) => (
                <optgroup key={g} label={g}>
                  {CATALOG.filter((p) => p.group === g).map((p) => (
                    <option key={p.id} value={p.id}>{p.name} · {p.tag} · {p.difficulty}</option>
                  ))}
                </optgroup>
              ))}
            </select>
          </Field>
          <Field label="Language">
            <select className="av-input" value={language} onChange={(e) => setLanguage(e.target.value)}>
              <option value="english">English</option>
              <option value="hindi">Hindi</option>
            </select>
          </Field>
        </>
      )}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <Field label="Seats per slot" hint="Max participants — slot shows Full at this count.">
          <input className="av-input" type="number" min={1} max={500} value={capacity}
            onChange={(e) => setCapacity(e.target.value)} />
        </Field>
        <Field label="Enrollment">
          <select className="av-input" value={enrollment} onChange={(e) => setEnrollment(e.target.value)}>
            <option value="open">Open — agents self-book</option>
            <option value="hybrid">Hybrid — you assign + self-book</option>
            <option value="assigned">Assigned only — closed session</option>
          </select>
        </Field>
      </div>
      <Field label="Title (optional)">
        <input className="av-input" value={title} onChange={(e) => setTitle(e.target.value)}
          placeholder="e.g. Personal-loan objection drill — Batch A" />
      </Field>
    </Modal>
  );
}

// ── Roster / report modal ────────────────────────────────────────────────────
function RosterModal({ slot, onClose, showToast }) {
  const [data, setData] = useState(null);          // roster or report payload
  const [agents, setAgents] = useState([]);
  const [pick, setPick] = useState('');
  const [busy, setBusy] = useState(false);
  const [agentReport, setAgentReport] = useState(null);
  const completed = slot.status === 'completed';

  const load = useCallback(async () => {
    try {
      const d = completed
        ? await sessions.report(slot.id)
        : await sessions.roster(slot.id);
      setData(d);
    } catch (e) { showToast(e.message, 'error'); }
  }, [slot.id, completed, showToast]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    admin.agents({ page_size: 200 }).then((d) => setAgents(d.users || d.items || [])).catch(() => {});
  }, []);

  const assign = async () => {
    if (!pick) return;
    setBusy(true);
    try {
      const d = await sessions.assign(slot.id, [pick]);
      if (d.skipped?.length) showToast(d.skipped[0].reason, 'error');
      else showToast('Agent assigned — they’ll see it under My sessions.');
      setPick('');
      await load();
    } catch (e) { showToast(e.message, 'error'); } finally { setBusy(false); }
  };

  const unassign = async (userId) => {
    setBusy(true);
    try { await sessions.unassign(slot.id, userId); await load(); }
    catch (e) { showToast(e.message, 'error'); } finally { setBusy(false); }
  };

  const roster = data?.roster || [];
  const summary = data?.summary;
  const active = roster.filter((r) => r.status !== 'cancelled');
  const inRoster = new Set(active.map((r) => r.user_id));
  const assignable = agents.filter((a) => !inRoster.has(a.id) && a.status === 'active');

  const stStyle = {
    booked:    { background: '#e8f0fa', color: '#2563a8' },
    attended:  { background: '#d1fae5', color: '#065f46' },
    no_show:   { background: '#fdeceb', color: '#b54534' },
    cancelled: { background: '#eef2f4', color: '#5a6b77' },
  };

  return (
    <Modal
      title={`${completed ? 'Session report' : 'Roster'} · ${fmtDay(slot.start_at)} ${fmtTime(slot.start_at)}–${fmtTime(slot.end_at)}`}
      onClose={onClose} width={720}
      footer={<Button variant="secondary" onClick={onClose}>Close</Button>}>
      {!data ? <div style={{ padding: 24, textAlign: 'center' }}><Spinner /> Loading…</div> : (
        <>
          {slot.title && <div style={{ fontWeight: 650, marginBottom: 10 }}>{slot.title}</div>}

          {summary && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 18, padding: '10px 14px',
              background: 'var(--av-subtle, #f6f8fc)', borderRadius: 10, marginBottom: 14, fontSize: 13 }}>
              <span><b>{summary.attended}</b> attended</span>
              <span><b>{summary.no_show}</b> no-show</span>
              <span><b>{summary.booked}</b> booked</span>
              <span>Avg score: <b>{summary.avg_score ?? '—'}</b></span>
              {summary.min_score != null && <span>Range: <b>{summary.min_score}–{summary.max_score}</b></span>}
            </div>
          )}
          {summary?.weak_areas?.length > 0 && (
            <div style={{ marginBottom: 14, fontSize: 12.5 }}>
              <b>Weakest areas across the session:</b>{' '}
              {summary.weak_areas.map((w) => `${w.parameter} (${w.avg_score})`).join(' · ')}
            </div>
          )}

          <div className="av-table-wrap" style={{ marginBottom: 14 }}>
            <table className="av-table">
              <thead><tr><th>Agent</th><th>Status</th><th>Score</th><th style={{ width: 140 }} /></tr></thead>
              <tbody>
                {active.length === 0 ? (
                  <tr><td colSpan={4} className="av-table-msg">No bookings yet.</td></tr>
                ) : active.map((r) => (
                  <tr key={r.id}>
                    <td><div className="av-cell-strong">{r.name || r.email}</div>
                      <div className="av-cell-sub">{r.email}{r.assigned ? ' · assigned' : ''}</div></td>
                    <td><span style={{ ...stStyle[r.status], borderRadius: 999, padding: '2px 10px',
                      fontSize: 11.5, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
                      {r.status.replace('_', '-')}</span></td>
                    <td>{r.score != null ? <b>{r.score}{r.grade ? ` (${r.grade})` : ''}</b> : '—'}</td>
                    <td>
                      <div className="av-row-actions">
                        {r.report_id && (
                          <button className="av-icon-btn" title="Open full report"
                            onClick={() => sessions.agentReport(r.report_id)
                              .then((d) => setAgentReport(d.session))
                              .catch((e) => showToast(e.message, 'error'))}>
                            <ClipboardList size={15} />
                          </button>
                        )}
                        {!completed && r.status === 'booked' && (
                          <button className="av-icon-btn" title="Remove from session"
                            disabled={busy} onClick={() => unassign(r.user_id)}>
                            <Trash2 size={15} />
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {!completed && slot.status === 'open' && (
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <UserPlus size={15} style={{ color: 'var(--av-muted, #5a6b77)' }} />
              <select className="av-input" style={{ flex: 1 }} value={pick} onChange={(e) => setPick(e.target.value)}>
                <option value="">Assign an agent…</option>
                {assignable.map((a) => <option key={a.id} value={a.id}>{a.name || a.email} ({a.email})</option>)}
              </select>
              <Button onClick={assign} loading={busy} disabled={!pick}>Assign</Button>
            </div>
          )}
        </>
      )}

      {agentReport && (
        <Modal title={`Report — ${agentReport.prospect_name || 'session'}`} width={640}
          onClose={() => setAgentReport(null)}
          footer={<Button variant="secondary" onClick={() => setAgentReport(null)}>Close</Button>}>
          <div style={{ fontSize: 13.5, marginBottom: 10 }}>
            Score: <b>{agentReport.score ?? '—'}</b>{agentReport.grade ? <> · Grade <b>{agentReport.grade}</b></> : null}
            {agentReport.loan_type ? <> · {agentReport.loan_type}</> : null}
          </div>
          {(agentReport.report?.parameters || []).map((p, i) => (
            <div key={i} style={{ padding: '8px 0', borderBottom: '1px solid var(--av-line, #e8edf5)', fontSize: 13 }}>
              <b>{p.name || p.parameter}</b>{p.score != null ? ` — ${p.score}` : ''}
              {p.feedback && <div style={{ color: 'var(--av-muted, #5a6b77)', marginTop: 2 }}>{p.feedback}</div>}
            </div>
          ))}
          {agentReport.report?.summary && (
            <p style={{ fontSize: 13, marginTop: 10 }}>{agentReport.report.summary}</p>
          )}
        </Modal>
      )}
    </Modal>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────────
export default function SessionsPage({ showToast }) {
  const [slots, setSlots] = useState(null);
  const [creating, setCreating] = useState(false);
  const [open, setOpen] = useState(null);          // slot for roster/report modal

  const load = useCallback(async () => {
    try {
      const from = new Date(); from.setDate(from.getDate() - 7); from.setHours(0, 0, 0, 0);
      const to = new Date(); to.setDate(to.getDate() + 21);
      const d = await sessions.list({ date_from: from.toISOString(), date_to: to.toISOString() });
      setSlots(d.slots || []);
    } catch (e) { showToast(e.message, 'error'); setSlots([]); }
  }, [showToast]);

  useEffect(() => { load(); }, [load]);

  const cancel = async (slot) => {
    if (!window.confirm('Cancel this session? Everyone booked will be released and notified.')) return;
    try { await sessions.cancel(slot.id); showToast('Session cancelled.'); load(); }
    catch (e) { showToast(e.message, 'error'); }
  };

  // Group by calendar day, upcoming first.
  const byDay = {};
  (slots || []).forEach((s) => {
    const day = new Date(s.start_at).toDateString();
    (byDay[day] = byDay[day] || []).push(s);
  });
  const days = Object.keys(byDay).sort((a, b) => new Date(a) - new Date(b));

  return (
    <div className="av-page">
      <div className="av-page-head">
        <div>
          <div className="av-page-title">Sessions</div>
          <div className="av-page-sub">Schedule role-play hours, manage seats and attendance, review session reports</div>
        </div>
        <div className="av-toolbar">
          <Button variant="secondary" onClick={load}><RefreshCw size={14} /> Refresh</Button>
          <Button onClick={() => setCreating(true)}><Plus size={15} /> New session</Button>
        </div>
      </div>

      {slots === null ? (
        <div className="av-card" style={{ padding: 30, textAlign: 'center' }}><Spinner /> Loading…</div>
      ) : days.length === 0 ? (
        <div className="av-card" style={{ padding: 30, textAlign: 'center', fontSize: 13.5, color: 'var(--av-muted, #5a6b77)' }}>
          No sessions in the last week or next three. Create the first one — every selected
          hour becomes a bookable slot agents see instantly.
        </div>
      ) : days.map((day) => (
        <div key={day} className="av-card" style={{ marginBottom: 14, padding: '14px 16px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontWeight: 700, marginBottom: 10 }}>
            <Calendar size={15} /> {fmtDay(byDay[day][0].start_at)}
          </div>
          <div className="av-table-wrap">
            <table className="av-table">
              <thead><tr>
                <th style={{ width: 130 }}>Time</th><th>Session</th><th style={{ width: 110 }}>Seats</th>
                <th style={{ width: 100 }}>Status</th><th style={{ width: 130 }} />
              </tr></thead>
              <tbody>
                {byDay[day].sort((a, b) => a.start_at.localeCompare(b.start_at)).map((s) => (
                  <tr key={s.id}>
                    <td style={{ fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' }}>
                      {fmtTime(s.start_at)} – {fmtTime(s.end_at)}
                    </td>
                    <td>
                      <div className="av-cell-strong">
                        {s.title || (s.kind === 'assistant' ? 'Scheduled role-play' : 'Practice hour')}
                      </div>
                      <div className="av-cell-sub">
                        {s.kind === 'assistant' ? 'Assistant' : 'Practice'} · {s.enrollment}
                        {s.scenario_ref ? ` · ${s.scenario_ref}` : ''}
                      </div>
                    </td>
                    <td style={{ fontVariantNumeric: 'tabular-nums' }}>
                      <span style={s.is_full ? { color: '#b54534', fontWeight: 700 } : undefined}>
                        {s.booked_count} / {s.capacity}{s.is_full ? ' · Full' : ''}
                      </span>
                    </td>
                    <td>
                      <span style={{
                        fontSize: 11.5, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.04em',
                        color: s.status === 'open' ? '#065f46' : s.status === 'completed' ? '#2563a8' : '#b54534',
                      }}>{s.status}</span>
                    </td>
                    <td>
                      <div className="av-row-actions">
                        <button className="av-icon-btn" title={s.status === 'completed' ? 'Session report' : 'Roster & assign'}
                          onClick={() => setOpen(s)}>
                          {s.status === 'completed' ? <BarChart3 size={15} /> : <Users size={15} />}
                        </button>
                        {s.status === 'open' && (
                          <button className="av-icon-btn" title="Cancel session" onClick={() => cancel(s)}>
                            <XCircle size={15} />
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}

      {creating && <CreateModal onClose={() => setCreating(false)} onCreated={load} showToast={showToast} />}
      {open && <RosterModal slot={open} onClose={() => { setOpen(null); load(); }} showToast={showToast} />}
    </div>
  );
}
