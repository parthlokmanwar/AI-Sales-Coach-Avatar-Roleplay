import React from "react";
import { PROFESSION_FIELDS } from "./formOptions";

export default function ProfessionFields({ profession, form, set }) {
  const fields = PROFESSION_FIELDS[profession];
  if (!fields?.length) return null;

  return (
    <div className="rp-profession-extra">
      <p className="rp-field-group-label">{profession} — Additional Details</p>
      {fields.map(f => (
        <div key={f.key} className="rp-field">
          <label className="rp-label">{f.label}</label>
          <input
            className="rp-input"
            type="text"
            placeholder={f.placeholder}
            value={form[f.key] || ""}
            onChange={e => set(f.key, e.target.value)}
          />
        </div>
      ))}
    </div>
  );
}
