// Book a Session — the agent side of the slot-booking system (Assistant mode).
//
// A week strip of days → each day lists its 1-hour slots with live seat counts
// (n/25), a Full state once capacity is reached, book/cancel actions, and a
// Join button that goes live 5 minutes before the slot starts. Joining an
// admin-planned (assistant) session launches the admin's pinned scenario;
// joining a practice seat opens the normal RolePlay chooser with the booking
// attached. All capacity rules are enforced server-side — this page only
// renders what /api/slots reports.

import { useState, useEffect, useCallback } from "react";
import {
  Calendar, Clock, Users, CheckCircle2, XCircle, PlayCircle, RefreshCw,
  ClipboardCheck,
} from "lucide-react";
import "./BookSession.css";

const API = "";

const fmtTime = (iso) =>
  new Date(iso).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" });
const fmtDay = (d) =>
  d.toLocaleDateString("en-IN", { weekday: "short", day: "2-digit", month: "short" });

function dayRange(date) {
  const start = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  const end = new Date(start.getTime() + 24 * 3600 * 1000);
  return { date_from: start.toISOString(), date_to: end.toISOString() };
}

const JOIN_EARLY_MS = 5 * 60 * 1000;

function joinState(slot) {
  const now = Date.now();
  const start = new Date(slot.start_at).getTime();
  const end = new Date(slot.end_at).getTime();
  if (now >= end) return "over";
  if (now >= start - JOIN_EARLY_MS) return "live";
  return "upcoming";
}

export default function BookSession({ onJoin }) {
  const days = [...Array(7)].map((_, i) => {
    const d = new Date();
    d.setDate(d.getDate() + i);
    return d;
  });
  const [dayIdx, setDayIdx] = useState(0);
  const [slots, setSlots] = useState([]);
  const [bookings, setBookings] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");        // slot id with an in-flight action
  const [error, setError] = useState("");
  // Re-render each 30s so Join buttons flip live without a manual refresh.
  const [, setTick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setTick((n) => n + 1), 30000);
    return () => clearInterval(t);
  }, []);

  const load = useCallback(async (idx = dayIdx) => {
    setLoading(true);
    setError("");
    try {
      const { date_from, date_to } = dayRange(days[idx]);
      const [slotsRes, mineRes] = await Promise.all([
        fetch(`${API}/api/slots?date_from=${encodeURIComponent(date_from)}&date_to=${encodeURIComponent(date_to)}`),
        fetch(`${API}/api/me/bookings`),
      ]);
      const slotsData = await slotsRes.json();
      const mineData = await mineRes.json();
      if (!slotsRes.ok) throw new Error(slotsData.detail || "Could not load sessions.");
      setSlots(slotsData.slots || []);
      if (mineRes.ok) setBookings(mineData.bookings || []);
    } catch (e) {
      setError(e.message || "Could not load sessions.");
    } finally {
      setLoading(false);
    }
  }, [dayIdx]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { load(dayIdx); }, [dayIdx]); // eslint-disable-line react-hooks/exhaustive-deps

  const act = async (slot, action) => {
    setBusy(slot.id);
    setError("");
    try {
      const r = await fetch(`${API}/api/slots/${slot.id}/${action}`, { method: "POST" });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.detail || `Request failed (${r.status})`);
      await load();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  };

  const join = (slot, booking) => {
    onJoin?.({
      booking_id: booking.id,
      kind: slot.kind,
      scenario_config: slot.scenario_config || {},
      title: slot.title || "",
    });
  };

  // Assistant mode is admin-led, so the page leads with what the ADMIN put on
  // this agent's plate, then their own booked seats, and only then the open
  // calendar. Sorted soonest-first; ended sessions drop off.
  const upcoming = bookings
    .filter((b) => b.slot && joinState(b.slot) !== "over")
    .sort((a, b) => (a.slot.start_at || "").localeCompare(b.slot.start_at || ""));
  const assigned = upcoming.filter((b) => b.assigned);
  const selfBooked = upcoming.filter((b) => !b.assigned);

  const sessionCard = (b, tone) => {
    const st = joinState(b.slot);
    return (
      <div key={b.id} className={`bs-mine-card bs-mine-card--${tone}${st === "live" ? " bs-mine-card--live" : ""}`}>
        <div className="bs-mine-body">
          <span className="bs-mine-title">
            {b.slot.title || (b.slot.kind === "assistant" ? "Scheduled role-play" : "Practice hour")}
            <span className={`bs-status-tag bs-status-tag--${st === "live" ? "live" : tone}`}>
              {st === "live" ? "live now" : tone === "assigned" ? "pending" : "booked"}
            </span>
          </span>
          <span className="bs-mine-when">
            {fmtDay(new Date(b.slot.start_at))} · {fmtTime(b.slot.start_at)} – {fmtTime(b.slot.end_at)}
          </span>
        </div>
        {st === "live" ? (
          <button className="bs-btn bs-btn--join" onClick={() => join(b.slot, b)}>
            <PlayCircle size={15} /> Join now
          </button>
        ) : (
          <span className="bs-mine-wait"><Clock size={13} /> Opens 5 min before start</span>
        )}
      </div>
    );
  };

  return (
    <div className="bs-page slide-up">
      <div className="bs-header">
        <span className="bs-header-icon"><Calendar size={22} /></span>
        <div>
          <h2 className="bs-title">My Sessions</h2>
          <p className="bs-subtitle">
            Role-play sessions your admin assigned to you, plus any open slot you
            book yourself. Join opens 5 minutes before a session starts.
          </p>
        </div>
        <button className="bs-refresh" onClick={() => load()} title="Refresh">
          <RefreshCw size={15} />
        </button>
      </div>

      {/* 1 ── What the admin expects of you. */}
      <div className="bs-mine">
        <div className="bs-section-label">
          <ClipboardCheck size={13} /> Assigned by your admin
        </div>
        {assigned.length === 0 && !loading ? (
          <div className="bs-mine-empty">
            Nothing assigned to you right now. Your admin can place you into a
            session — until then, book any open slot below.
          </div>
        ) : (
          assigned.map((b) => sessionCard(b, "assigned"))
        )}
      </div>

      {/* 2 ── Seats this agent took themselves. */}
      {selfBooked.length > 0 && (
        <div className="bs-mine">
          <div className="bs-section-label">
            <CheckCircle2 size={13} /> Slots you booked
          </div>
          {selfBooked.map((b) => sessionCard(b, "booked"))}
        </div>
      )}

      {/* 3 ── The open calendar, secondary to the two blocks above. */}
      <div className="bs-section-label" style={{ marginTop: 4 }}>Book another slot</div>
      <div className="bs-days">
        {days.map((d, i) => (
          <button
            key={i}
            className={`bs-day${i === dayIdx ? " bs-day--active" : ""}`}
            onClick={() => setDayIdx(i)}
          >
            <span className="bs-day-name">{i === 0 ? "Today" : d.toLocaleDateString("en-IN", { weekday: "short" })}</span>
            <span className="bs-day-date">{d.getDate()} {d.toLocaleDateString("en-IN", { month: "short" })}</span>
          </button>
        ))}
      </div>

      {error && <div className="bs-error">{error}</div>}

      {loading ? (
        <div className="bs-empty"><div className="spinner" style={{ width: 26, height: 26 }} /></div>
      ) : slots.length === 0 ? (
        <div className="bs-empty">
          No sessions scheduled for this day yet. Your admin publishes the calendar —
          check another day or ask them to open slots.
        </div>
      ) : (
        <div className="bs-slots">
          {slots.map((s) => {
            const mine = s.my_booking;
            const st = joinState(s);
            const pct = Math.min(100, Math.round((s.booked_count / s.capacity) * 100));
            return (
              <div key={s.id} className={`bs-slot${s.is_full && !mine ? " bs-slot--full" : ""}`}>
                <div className="bs-slot-time">
                  <span className="bs-slot-hours">{fmtTime(s.start_at)} – {fmtTime(s.end_at)}</span>
                  <span className={`bs-kind bs-kind--${s.kind}`}>
                    {s.kind === "assistant" ? "Assistant" : "Practice"}
                  </span>
                </div>
                <div className="bs-slot-body">
                  <span className="bs-slot-title">
                    {s.title || (s.kind === "assistant" ? "Scheduled role-play" : "Practice hour")}
                  </span>
                  <div className="bs-slot-cap">
                    <Users size={13} />
                    {s.is_full
                      ? <span className="bs-full-text">Full — {s.booked_count} / {s.capacity}</span>
                      : <span>{s.booked_count} / {s.capacity} booked</span>}
                  </div>
                  <div className="bs-slot-bar"><span style={{ width: `${pct}%` }} className={s.is_full ? "bs-bar-full" : ""} /></div>
                </div>
                <div className="bs-slot-actions">
                  {mine ? (
                    <>
                      <span className="bs-booked-chip"><CheckCircle2 size={13} /> Booked</span>
                      {st === "live" && (
                        <button className="bs-btn bs-btn--join" onClick={() => join(s, mine)}>
                          <PlayCircle size={14} /> Join
                        </button>
                      )}
                      {st === "upcoming" && !mine.assigned && (
                        <button
                          className="bs-btn bs-btn--ghost"
                          disabled={busy === s.id}
                          onClick={() => act(s, "cancel-booking")}
                        >
                          <XCircle size={14} /> Cancel
                        </button>
                      )}
                      {st === "upcoming" && mine.assigned && (
                        <span className="bs-assigned-note">Assigned by admin</span>
                      )}
                    </>
                  ) : s.is_full ? (
                    <span className="bs-full-badge">Full</span>
                  ) : st === "over" ? (
                    <span className="bs-over">Ended</span>
                  ) : (
                    <button
                      className="bs-btn bs-btn--book"
                      disabled={busy === s.id}
                      onClick={() => act(s, "book")}
                    >
                      {busy === s.id ? "Booking…" : "Book seat"}
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
