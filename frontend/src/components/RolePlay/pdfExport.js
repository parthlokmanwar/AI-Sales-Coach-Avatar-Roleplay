// Direct PDF download using jsPDF + html2canvas.
// No print dialog — clicking "Download PDF" immediately saves a .pdf file.

const GRADE_TEXT = {
  A: "#065f46", B: "#1e40af", C: "#92400e", D: "#9a3412", F: "#991b1b",
};
const GRADE_BG = {
  A: "#d1fae5", B: "#dbeafe", C: "#fef3c7", D: "#fed7aa", F: "#fee2e2",
};
const GRADE_BORDER = {
  A: "#6ee7b7", B: "#93c5fd", C: "#fcd34d", D: "#fb923c", F: "#fca5a5",
};

const TAG_TEXT = {
  "GOOD": "#065f46", "NEEDS WORK": "#92400e",
  "CRITICAL ERROR": "#991b1b", "NEUTRAL": "#6b7280",
};
const TAG_BG = {
  "GOOD": "#d1fae5", "NEEDS WORK": "#fef3c7",
  "CRITICAL ERROR": "#fee2e2", "NEUTRAL": "#f3f4f6",
};

const PARAM_LABELS = {
  tone_professionalism:   "Tone & Professionalism",
  product_knowledge:      "Product Knowledge",
  objection_handling:     "Objection Handling",
  listening_empathy:      "Listening & Empathy",
  pitch_structure:        "Pitch Structure & Flow",
  closing_technique:      "Closing Technique",
  language_communication: "Language & Communication",
};

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function buildReportHTML(report, form) {
  const grade = report.overall_grade || "C";

  const lines    = Array.isArray(report.lines) ? report.lines : [];
  const repLines = lines.filter(l => l.speaker === "Rep");
  const criticalCount = repLines.filter(l => l.tag === "CRITICAL ERROR").length;
  const goodCount     = repLines.filter(l => l.tag === "GOOD").length;
  const needsCount    = repLines.filter(l => l.tag === "NEEDS WORK").length;

  const paramsHTML = Object.entries(report.parameters || {}).map(([key, val]) => `
    <tr>
      <td style="padding:8px 12px;font-weight:600;color:#374151;border-bottom:1px solid #f3f4f6">
        ${esc(PARAM_LABELS[key] || key)}
      </td>
      <td style="padding:8px 12px;border-bottom:1px solid #f3f4f6">
        <div style="background:#e5e7eb;border-radius:4px;height:8px;width:200px">
          <div style="background:${val.score >= 8 ? "#22c55e" : val.score >= 6 ? "#f59e0b" : "#ef4444"};
            height:8px;border-radius:4px;width:${(Number(val.score) / 10) * 100}%"></div>
        </div>
      </td>
      <td style="padding:8px 12px;font-weight:700;color:#1f2937;border-bottom:1px solid #f3f4f6">${esc(val.score)}/10</td>
      <td style="padding:8px 12px;color:#6b7280;font-size:13px;border-bottom:1px solid #f3f4f6">${esc(val.verdict)}</td>
    </tr>
  `).join("");

  const linesHTML = lines.map(line => {
    const isRep = line.speaker === "Rep";
    const tagStyle = isRep && line.tag && line.tag !== "NEUTRAL"
      ? `background:${TAG_BG[line.tag]};color:${TAG_TEXT[line.tag]};padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600`
      : "";
    return `
      <div style="margin-bottom:12px;padding:10px 12px;
        border-left:3px solid ${isRep ? "#e53e3e" : "#9ca3af"};
        background:${isRep ? "#fff" : "#fafafa"};border-radius:0 6px 6px 0">
        <div style="display:flex;gap:8px;align-items:center;margin-bottom:4px;flex-wrap:wrap">
          <span style="font-size:11px;color:#9ca3af;font-family:monospace">${esc(line.timestamp)}</span>
          <span style="font-size:12px;font-weight:700;color:${isRep ? "#e53e3e" : "#6b7280"}">${esc(line.speaker)}</span>
          ${isRep && line.tag ? `<span style="${tagStyle}">${esc(line.tag)}</span>` : ""}
        </div>
        <p style="margin:0 0 4px;font-size:13px;color:#1f2937">${esc(line.text)}</p>
        ${isRep && line.comment
          ? `<p style="margin:0;font-size:12px;color:#6b7280;font-style:italic">💬 ${esc(line.comment)}</p>`
          : ""}
      </div>
    `;
  }).join("");

  const coachingHTML = (report.coaching_notes || []).map((note, i) => `
    <div style="display:flex;gap:10px;margin-bottom:10px;align-items:flex-start">
      <span style="min-width:22px;height:22px;background:#e53e3e;color:#fff;border-radius:50%;
        display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700">${i + 1}</span>
      <p style="margin:0;font-size:13px;color:#374151;line-height:1.6">${esc(note)}</p>
    </div>
  `).join("");

  const bestHTML = (report.best_moments || []).map(m => `
    <div style="margin-bottom:10px;padding:10px;background:#d1fae5;border-radius:6px">
      <span style="font-size:11px;color:#065f46;font-family:monospace">${esc(m.timestamp)}</span>
      <p style="margin:4px 0;font-size:13px;color:#065f46;font-style:italic">"${esc(m.quote)}"</p>
      <p style="margin:0;font-size:12px;color:#047857">${esc(m.reason)}</p>
    </div>
  `).join("");

  const worstHTML = (report.worst_moments || []).map(m => `
    <div style="margin-bottom:10px;padding:10px;background:#fee2e2;border-radius:6px">
      <span style="font-size:11px;color:#991b1b;font-family:monospace">${esc(m.timestamp)}</span>
      <p style="margin:4px 0;font-size:13px;color:#991b1b;font-style:italic">"${esc(m.quote)}"</p>
      <p style="margin:0;font-size:12px;color:#b91c1c">${esc(m.reason)}</p>
    </div>
  `).join("");

  const statsHTML = [
    ["Good Moments",    goodCount,        "#d1fae5", "#065f46"],
    ["Needs Work",      needsCount,       "#fef3c7", "#92400e"],
    ["Critical Errors", criticalCount,    "#fee2e2", "#991b1b"],
    ["Rep Lines Total", repLines.length,  "#f3f4f6", "#374151"],
  ].map(([label, val, bg, color]) => `
    <div style="padding:14px;border-radius:8px;background:${bg};text-align:center;flex:1">
      <div style="font-size:26px;font-weight:900;color:${color}">${val}</div>
      <div style="font-size:11px;color:${color};font-weight:600;margin-top:4px">${label}</div>
    </div>
  `).join("");

  const amountStr = form.loan_amount ? ` · ₹${esc(form.loan_amount)}L` : "";

  return `
    <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#1f2937;padding:32px;max-width:794px;margin:0 auto">

      <!-- HEADER -->
      <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:20px;padding-bottom:16px;border-bottom:2px solid #e5e7eb">
        <div>
          <h1 style="margin:0 0 4px;font-size:20px">📊 Call Performance Report</h1>
          <p style="margin:0;color:#6b7280;font-size:13px">
            ${esc(form.prospect_name)} · ${esc(form.profession)} · ${esc(form.loan_type)}${amountStr}
            · Duration: ${esc(report.call_duration)}
          </p>
        </div>
        <div style="text-align:center;padding:12px 20px;border-radius:10px;
          background:${GRADE_BG[grade] || "#f3f4f6"};
          border:2px solid ${GRADE_BORDER[grade] || "#e5e7eb"}">
          <div style="font-size:32px;font-weight:900;color:${GRADE_TEXT[grade] || "#374151"}">${esc(grade)}</div>
          <div style="font-size:13px;font-weight:700;color:#374151">${esc(report.overall_score)}/100</div>
        </div>
      </div>

      <!-- QUICK STATS -->
      <div style="display:flex;gap:10px;margin-bottom:24px">${statsHTML}</div>

      <!-- PARAMETERS -->
      <h2 style="font-size:15px;color:#374151;margin:0 0 10px">Parameter Scores</h2>
      <table style="width:100%;border-collapse:collapse;margin-bottom:24px">${paramsHTML}</table>

      <!-- VERDICT -->
      <h2 style="font-size:15px;color:#374151;margin:0 0 10px">Overall Verdict</h2>
      <div style="padding:14px;background:#f8fafc;border-left:4px solid #e53e3e;
        border-radius:0 6px 6px 0;font-size:13px;line-height:1.7;color:#374151;margin-bottom:24px">
        ${esc(report.overall_verdict)}
      </div>

      <!-- KEY MOMENTS -->
      <h2 style="font-size:15px;color:#374151;margin:0 0 10px">Best Moments</h2>
      <div style="margin-bottom:24px">${bestHTML}</div>
      <h2 style="font-size:15px;color:#374151;margin:0 0 10px">Worst Moments</h2>
      <div style="margin-bottom:24px">${worstHTML}</div>

      <!-- COACHING -->
      <h2 style="font-size:15px;color:#374151;margin:0 0 10px">Manager Coaching Notes</h2>
      <div style="margin-bottom:24px">${coachingHTML}</div>

      <!-- TRANSCRIPT -->
      <h2 style="font-size:15px;color:#374151;margin:0 0 10px">Line-by-Line Transcript</h2>
      <div>${linesHTML}</div>

    </div>
  `;
}

export async function downloadReportPDF(report, form) {
  const container = document.createElement("div");
  container.style.cssText =
    "position:fixed;top:0;left:-9999px;width:794px;background:#fff;";
  container.innerHTML = buildReportHTML(report, form);
  document.body.appendChild(container);

  try {
    const [{ jsPDF }, html2canvas] = await Promise.all([
      import("jspdf"),
      import("html2canvas"),
    ]);

    const canvas = await html2canvas.default(container, {
      scale: 2,
      useCORS: true,
      logging: false,
      backgroundColor: "#ffffff",
    });

    const imgData = canvas.toDataURL("image/jpeg", 0.92);
    const pdf = new jsPDF({ unit: "mm", format: "a4", orientation: "portrait" });

    const pageW  = pdf.internal.pageSize.getWidth();
    const pageH  = pdf.internal.pageSize.getHeight();
    const margin = 10;
    const imgW   = pageW - margin * 2;
    const imgH   = (canvas.height * imgW) / canvas.width;

    let y = margin;
    let remaining = imgH;

    // Slice the canvas into A4 pages
    while (remaining > 0) {
      const sliceH = Math.min(remaining, pageH - margin * 2);
      const srcY   = (imgH - remaining) * (canvas.height / imgH);
      const srcH   = sliceH * (canvas.height / imgH);

      const pageCanvas = document.createElement("canvas");
      pageCanvas.width  = canvas.width;
      pageCanvas.height = srcH;
      pageCanvas.getContext("2d").drawImage(
        canvas, 0, srcY, canvas.width, srcH, 0, 0, canvas.width, srcH
      );

      const pageImg = pageCanvas.toDataURL("image/jpeg", 0.92);
      pdf.addImage(pageImg, "JPEG", margin, margin, imgW, sliceH);

      remaining -= sliceH;
      if (remaining > 0) pdf.addPage();
    }

    const filename = `Call_Report_${(form.prospect_name || "report").replace(/[^a-z0-9]/gi, "_")}.pdf`;
    pdf.save(filename);
  } finally {
    document.body.removeChild(container);
  }
}
