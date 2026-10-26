import React, { useEffect, useRef } from "react";
import {
  ChevronLeft, ChevronRight, Plus,
  Volume2, VolumeX, Maximize2, X,
} from "lucide-react";
import { PRESETS, EXISTING_PRESETS, ETB_SERVICE_PRESETS, BRIEFS, ETB_BRIEFS } from "./presets";
import { ROLEPLAY_MODEL } from "./avatarMap";
import { avatarStill } from "./avatarStills";
import { mediaUrl } from "../../config";

const DIFF_STYLE = {
  Easy:   { bg: "#d1fae5", color: "#065f46" },
  Medium: { bg: "#fef3c7", color: "#92400e" },
  Hard:   { bg: "#fee2e2", color: "#991b1b" },
  Expert: { bg: "#ede9fe", color: "#5b21b6" },
};

// Order + labels for the ETB service-call row's feedback-state sub-groups.
const ETB_FEEDBACK_ORDER = [
  { state: "Happy",     cls: "happy",     label: "😊 Happy" },
  { state: "Neutral",   cls: "neutral",   label: "😐 Neutral" },
  { state: "Complaint", cls: "complaint", label: "😠 Complaint" },
];

// Rep-facing "coaching brief" modal — full detail on who the customer is and
// how to handle them during the pitch.
function PresetDetail({ preset, replica, onClose, onJoin, loading }) {
  const key = preset.briefId || preset.id;
  const brief = ETB_BRIEFS[key] || BRIEFS[key] || {};
  return (
    <div className="rp-modal-overlay" onClick={onClose}>
      <div className="rp-modal" onClick={(e) => e.stopPropagation()}>
        <button className="rp-modal-close" onClick={onClose} aria-label="Close">
          <X size={18} />
        </button>

        <div className="rp-modal-head">
          {(() => {
            const face = avatarStill(preset.replica_id) || mediaUrl(replica?.image || "");
            return face
              ? <img className="rp-modal-face" src={face} alt={preset.name} />
              : <div className="rp-modal-face rp-modal-face--ph">
                  {(preset.name || "?").charAt(0)}
                </div>;
          })()}
          <div>
            <h3 className="rp-modal-name">{preset.name}</h3>
            <p className="rp-modal-tag">{preset.tag}</p>
            <span className="rp-modal-diff">{preset.difficulty} difficulty</span>
          </div>
        </div>

        <div className="rp-modal-body">
          <section className="rp-modal-sec">
            <p className="rp-modal-label">Who they are</p>
            <p className="rp-modal-text">{brief.profile}</p>
          </section>

          <section className="rp-modal-sec">
            <p className="rp-modal-label">What they want</p>
            <p className="rp-modal-text">{brief.goal}</p>
          </section>

          <section className="rp-modal-sec">
            <p className="rp-modal-label rp-modal-label--warn">Watch out for</p>
            <ul className="rp-modal-list">
              {(brief.concerns || []).map((c, i) => <li key={i}>{c}</li>)}
            </ul>
          </section>

          <section className="rp-modal-sec">
            <p className="rp-modal-label">How they talk</p>
            <p className="rp-modal-text">{brief.style}</p>
          </section>

          <section className="rp-modal-sec">
            <p className="rp-modal-label rp-modal-label--good">How to win them over</p>
            <ul className="rp-modal-list">
              {(brief.tips || []).map((t, i) => <li key={i}>{t}</li>)}
            </ul>
          </section>
        </div>

        <div className="rp-modal-foot">
          <button className="rp-btn-secondary" onClick={onClose}>Close</button>
          <button
            className="rp-preset-join rp-modal-join"
            onClick={() => onJoin(preset)}
            disabled={loading}
          >
            {loading ? "Connecting…" : "Join Call →"}
          </button>
        </div>
      </div>
    </div>
  );
}

function PresetCard({ preset, replica, audioOn, onToggleAudio, onJoin, onSeeMore, loading }) {
  const vidRef = useRef(null);
  const video = mediaUrl(replica?.video || "");
  // Prefer a bundled static still when the replica's live CDN image is missing/403
  // (also the reliable poster while the proxied video loads / when it can't).
  const image = avatarStill(preset.replica_id) || mediaUrl(replica?.image || "");
  const initial = (preset.name || "?").trim().charAt(0).toUpperCase();
  const diff = DIFF_STYLE[preset.difficulty] || DIFF_STYLE.Medium;

  // Keep this card's audio in sync with the single "active audio" selection.
  useEffect(() => {
    if (vidRef.current) vidRef.current.muted = !audioOn;
  }, [audioOn]);

  const goFullscreen = () => {
    const v = vidRef.current;
    if (!v) return;
    onToggleAudio(preset.id, true);   // ensure it has sound in fullscreen
    v.muted = false;
    (v.requestFullscreen || v.webkitRequestFullscreen ||
     v.webkitEnterFullscreen || (() => {})).call(v);
  };

  return (
    <div className="rp-preset-card">
      <div className="rp-preset-video-wrap">
        {video ? (
          <video
            ref={vidRef}
            className="rp-preset-video"
            src={video}
            poster={image || undefined}
            autoPlay loop muted playsInline preload="metadata"
          />
        ) : image ? (
          <img className="rp-preset-video" src={image} alt={preset.name} />
        ) : (
          <div className="rp-preset-video rp-preset-video--ph">{initial}</div>
        )}

        <span className="rp-preset-diff" style={{ background: diff.bg, color: diff.color }}>
          {preset.difficulty}
        </span>

        {video && (
          <div className="rp-preset-vidbtns">
            <button
              className="rp-preset-vidbtn"
              onClick={() => onToggleAudio(preset.id)}
              title={audioOn ? "Mute" : "Listen to voice"}
              aria-label={audioOn ? "Mute" : "Listen to voice"}
            >
              {audioOn ? <Volume2 size={15} /> : <VolumeX size={15} />}
            </button>
            <button
              className="rp-preset-vidbtn"
              onClick={goFullscreen}
              title="Fullscreen (with sound)"
              aria-label="Fullscreen"
            >
              <Maximize2 size={15} />
            </button>
          </div>
        )}
      </div>

      <div className="rp-preset-body">
        <p className="rp-preset-name">{preset.name}</p>
        <p className="rp-preset-tag">{preset.tag}</p>
        <p className="rp-preset-blurb">{preset.blurb}</p>
        <button className="rp-preset-more" onClick={() => onSeeMore(preset)}>
          See full brief →
        </button>
        <button
          className="rp-preset-join"
          onClick={() => onJoin(preset)}
          disabled={loading}
        >
          {loading ? "Connecting…" : "Join Call →"}
        </button>
      </div>
    </div>
  );
}

export default function PresetGallery({
  avatars, models, avatarsLoading, avatarsError,
  loading, error, onLoadAvatars, onJoinPreset, onCustom, onCustomExisting, onCustomEtb,
}) {
  const trackNewRef      = useRef(null);
  const trackExistingRef = useRef(null);
  const trackEtbRef      = useRef(null);
  const [audioId, setAudioId] = React.useState(null);   // only one card plays sound
  const [detail, setDetail]   = React.useState(null);   // preset shown in the brief modal
  const [language, setLanguage] = React.useState("english"); // call language for any preset

  // Load the Tavus avatars (for the pinned faces' sample videos) on mount.
  useEffect(() => { onLoadAvatars?.(); }, [onLoadAvatars]);

  // Map replica_id → replica object (video/image) from the fetched list.
  const byId = React.useMemo(() => {
    const m = {};
    (avatars || []).forEach(a => { m[a.id] = a; });
    return m;
  }, [avatars]);

  const toggleAudio = (id, force) => {
    setAudioId(prev => (force ? id : (prev === id ? null : id)));
  };

  const scrollByPage = (ref, dir) => {
    const el = ref.current;
    if (el) el.scrollBy({ left: dir * el.clientWidth, behavior: "smooth" });
  };

  // Feature the newest avatars first in BOTH rows, in this order, keeping the
  // rest in their natural order. Matches on the base preset id — existing-row
  // items carry it as `briefId` (their own id has a "-existing" suffix), new-row
  // items as `id`.
  const FEATURED_IDS = ["self-employed-m", "housewife-f"];
  const featuredFirst = (list) => {
    const key = (p) => p.briefId || p.id;
    const featured = FEATURED_IDS
      .map(id => list.find(p => key(p) === id))
      .filter(Boolean);
    const rest = list.filter(p => !FEATURED_IDS.includes(key(p)));
    return [...featured, ...rest];
  };
  const newCustomers      = featuredFirst(PRESETS);
  const existingCustomers = featuredFirst(EXISTING_PRESETS);

  // ETB service-call presets grouped by feedback state (Happy / Neutral / Complaint)
  // so the gallery can show the three-mood structure within one scrolling row.
  const etbByState = React.useMemo(() => {
    const g = {};
    (ETB_SERVICE_PRESETS || []).forEach(p => {
      const s = p.feedbackState || p.form?.feedback_state || "Other";
      (g[s] = g[s] || []).push(p);
    });
    return g;
  }, []);

  const renderCard = (preset) => (
    <PresetCard
      key={preset.id}
      preset={preset}
      replica={byId[preset.replica_id]}
      audioOn={audioId === preset.id}
      onToggleAudio={toggleAudio}
      onJoin={handleJoin}
      onSeeMore={setDetail}
      loading={loading}
    />
  );

  // Each preset is pinned to a specific replica_id and uses the shared gpt-4o-mini
  // avatar model (ROLEPLAY_MODEL). The SAME avatars are reused for both languages:
  // the replica is just a face, so we only pass the selected `language` and the
  // backend layers on the Hindi prompt + gender-matched ElevenLabs voice (derived
  // from preset.form.gender) at launch. Hindi must NOT reuse a pinned English
  // persona (it would skip the Hindi voice/prompt), so persona_id is dropped for
  // Hindi and a fresh Hindi persona is built per call.
  const handleJoin = (preset) => {
    onJoinPreset({
      ...preset.form,
      difficulty: preset.difficulty,
      customer_type: preset.customer_type || "new",
      replica_id: preset.replica_id,
      persona_id: language === "hindi" ? "" : (preset.persona_id || ""),
      model: ROLEPLAY_MODEL,
      language,
    });
  };

  return (
    <div className="rp-preset-wrap">

      <div className="rp-preset-head">
        <div>
          <p className="rp-preset-title">Pick a customer to practise with</p>
          <p className="rp-preset-sub">
            Tap 🔊 to hear their voice, ⛶ to preview fullscreen, then Join. Swipe for more.
          </p>
        </div>

        {/* Call language — same avatars, but Hindi makes the customer speak
            natural Hinglish (sample preview videos stay English). */}
        <div className="rp-preset-lang">
          <span className="rp-preset-lang-label">Call language</span>
          <div className="rp-lang-toggle">
            {[
              { value: "english", label: "English" },
              { value: "hindi",   label: "हिंदी / Hindi" },
            ].map(opt => (
              <button
                key={opt.value}
                type="button"
                className={
                  "rp-lang-btn" + (language === opt.value ? " rp-lang-btn--active" : "")
                }
                onClick={() => setLanguage(opt.value)}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {avatarsError && <div className="rp-error">{avatarsError}</div>}
      {error && <div className="rp-error">{error}</div>}

      {/* ── Row 1: New customers (no HDFC relationship) ── */}
      <div className="rp-preset-section">
        <div className="rp-preset-rowhead">
          <p className="rp-preset-rowtitle">New Customers</p>
          <div className="rp-preset-nav">
            <button className="rp-preset-arrow" onClick={() => scrollByPage(trackNewRef, -1)} aria-label="Previous">
              <ChevronLeft size={18} />
            </button>
            <button className="rp-preset-arrow" onClick={() => scrollByPage(trackNewRef, 1)} aria-label="Next">
              <ChevronRight size={18} />
            </button>
          </div>
        </div>
        <div className="rp-preset-track" ref={trackNewRef}>
          {newCustomers.map(renderCard)}

          {/* Custom scenario — build your own from the full form */}
          <div className="rp-preset-card rp-preset-card--custom" onClick={onCustom}>
            <div className="rp-preset-custom-inner">
              <span className="rp-preset-custom-icon"><Plus size={26} /></span>
              <p className="rp-preset-name">Custom Scenario</p>
              <p className="rp-preset-blurb">
                Build your own prospect — choose the profile, loan and situation.
              </p>
              <span className="rp-preset-custom-cta">Create your own →</span>
            </div>
          </div>
        </div>
      </div>

      {/* ── Row 2: Existing HDFC customers ── */}
      <div className="rp-preset-section">
        <div className="rp-preset-rowhead">
          <p className="rp-preset-rowtitle">Existing Customers — Follow-up Call</p>
          <div className="rp-preset-nav">
            <button className="rp-preset-arrow" onClick={() => scrollByPage(trackExistingRef, -1)} aria-label="Previous">
              <ChevronLeft size={18} />
            </button>
            <button className="rp-preset-arrow" onClick={() => scrollByPage(trackExistingRef, 1)} aria-label="Next">
              <ChevronRight size={18} />
            </button>
          </div>
        </div>
        <div className="rp-preset-track" ref={trackExistingRef}>
          {existingCustomers.map(renderCard)}

          {/* Custom follow-up scenario — build your own existing customer */}
          <div className="rp-preset-card rp-preset-card--custom" onClick={onCustomExisting}>
            <div className="rp-preset-custom-inner">
              <span className="rp-preset-custom-icon"><Plus size={26} /></span>
              <p className="rp-preset-name">Custom Scenario</p>
              <p className="rp-preset-blurb">
                Build your own existing HDFC customer — set their profile, loyalty situation and offer.
              </p>
              <span className="rp-preset-custom-cta">Create your own →</span>
            </div>
          </div>
        </div>
      </div>

      {/* ── Row 3: Existing customers — SERVICE CALL (ETB) ── */}
      <div className="rp-preset-section">
        <div className="rp-preset-rowhead">
          <p className="rp-preset-rowtitle">Existing Customers — Service Call</p>
          <div className="rp-preset-nav">
            <button className="rp-preset-arrow" onClick={() => scrollByPage(trackEtbRef, -1)} aria-label="Previous">
              <ChevronLeft size={18} />
            </button>
            <button className="rp-preset-arrow" onClick={() => scrollByPage(trackEtbRef, 1)} aria-label="Next">
              <ChevronRight size={18} />
            </button>
          </div>
        </div>
        <p className="rp-preset-sub" style={{ marginBottom: 4 }}>
          You (the RM) cold-call an existing customer about a pre-approved offer they
          didn’t ask for. Handle any grievance first, then earn the offer.
        </p>
        <div className="rp-preset-track" ref={trackEtbRef}>
          {ETB_FEEDBACK_ORDER.map(({ state, cls, label }) => {
            const cards = etbByState[state] || [];
            if (!cards.length) return null;
            return (
              <React.Fragment key={state}>
                <div className={"rp-etb-group-label rp-etb-group-label--" + cls}>{label}</div>
                {cards.map(renderCard)}
              </React.Fragment>
            );
          })}

          {/* Custom ETB service-call scenario — build your own */}
          <div className="rp-preset-card rp-preset-card--custom" onClick={onCustomEtb}>
            <div className="rp-preset-custom-inner">
              <span className="rp-preset-custom-icon"><Plus size={26} /></span>
              <p className="rp-preset-name">Custom Service Call</p>
              <p className="rp-preset-blurb">
                Build your own existing-customer service call — set their mood, grievance and offer.
              </p>
              <span className="rp-preset-custom-cta">Create your own →</span>
            </div>
          </div>
        </div>
      </div>

      {avatarsLoading && <p className="rp-preset-loading">Loading avatar previews…</p>}

      {detail && (
        <PresetDetail
          preset={detail}
          replica={byId[detail.replica_id]}
          onClose={() => setDetail(null)}
          onJoin={(p) => { setDetail(null); handleJoin(p); }}
          loading={loading}
        />
      )}
    </div>
  );
}
