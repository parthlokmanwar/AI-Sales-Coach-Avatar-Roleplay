import React from "react";

export default function AvatarPicker({
  form, set,
  avatars, models, avatarsLoading, avatarsError,
  onJoin, onBack, loading, error, canJoin,
}) {
  return (
    <div className="rp-avatar-wrap">

      <div className="rp-group">
        <p className="rp-group-title">Choose Avatar</p>
        <p className="rp-group-hint">
          Pick the face your sales rep will talk to. This is the customer they’ll
          practise against on the live call.
        </p>

        {avatarsLoading && (
          <div className="rp-av-loading">Loading avatars…</div>
        )}

        {avatarsError && (
          <div className="rp-error">{avatarsError}</div>
        )}

        {!avatarsLoading && !avatarsError && avatars.length === 0 && (
          <div className="rp-error">
            No avatars found on this Tavus account.
          </div>
        )}

        {!avatarsLoading && avatars.length > 0 && (
          <div className="rp-field">
            <select
              className="rp-select"
              value={form.replica_id}
              onChange={e => set("replica_id", e.target.value)}
            >
              {avatars.map(a => (
                <option key={a.id} value={a.id}>{a.label}</option>
              ))}
            </select>
          </div>
        )}
      </div>

      <div className="rp-group">
        <p className="rp-group-title">Avatar Brain (LLM)</p>
        <p className="rp-group-hint">
          The model that powers the customer’s responses during the call.
        </p>
        <div className="rp-field">
          <select
            className="rp-select"
            value={form.model}
            onChange={e => set("model", e.target.value)}
          >
            {Object.keys(models).length === 0 && (
              <option value="">— no models —</option>
            )}
            {Object.entries(models).map(([label, id]) => (
              <option key={id} value={id}>{label}</option>
            ))}
          </select>
        </div>
      </div>

      {error && <div className="rp-error">{error}</div>}

      <div className="rp-action-row">
        <button className="rp-btn-secondary" onClick={onBack} disabled={loading}>
          ← Edit Form
        </button>
        <button
          className="rp-btn-primary"
          style={{ width: "auto" }}
          onClick={onJoin}
          disabled={loading || !canJoin}
        >
          {loading ? "Connecting…" : "Join Call →"}
        </button>
      </div>
    </div>
  );
}
