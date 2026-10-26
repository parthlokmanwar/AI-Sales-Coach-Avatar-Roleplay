// Admin/platform API client. All requests ride the global fetch interceptor in
// auth.js (which attaches the Bearer token and bounces to login on 401), so
// these helpers only deal with shaping requests and surfacing errors.

async function req(method, url, body, isForm = false) {
  const opts = { method, headers: {} };
  if (isForm) {
    opts.body = body; // FormData — browser sets the multipart boundary
  } else if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(url, opts);
  let data = {};
  try { data = await res.json(); } catch { /* empty / non-json */ }
  if (!res.ok || data.success === false) {
    throw new Error(data.detail || `Request failed (${res.status})`);
  }
  return data;
}

// Authenticated file download — uses fetch (so the interceptor attaches the
// Bearer token), then triggers a browser save from the blob.
export async function downloadFile(url, filename) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Download failed (${res.status})`);
  const blob = await res.blob();
  const href = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = href; a.download = filename;
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(href);
}

const qs = (params = {}) =>
  Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== null && v !== '')
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
    .join('&');

// ── Super Admin (platform) ─────────────────────────────────────────────────
export const platform = {
  stats: () => req('GET', '/api/platform/stats'),
  companies: (params) => req('GET', `/api/platform/companies?${qs(params)}`),
  createCompany: (b) => req('POST', '/api/platform/companies', b),
  getCompany: (id) => req('GET', `/api/platform/companies/${id}`),
  updateCompany: (id, b) => req('PATCH', `/api/platform/companies/${id}`, b),
  enableCompany: (id) => req('POST', `/api/platform/companies/${id}/enable`),
  disableCompany: (id) => req('POST', `/api/platform/companies/${id}/disable`),
  deleteCompany: (id) => req('DELETE', `/api/platform/companies/${id}`),
  companyUsers: (id, params) => req('GET', `/api/platform/companies/${id}/users?${qs(params)}`),
  createAdmin: (id, b) => req('POST', `/api/platform/companies/${id}/admins`, b),
  audit: (params) => req('GET', `/api/platform/audit?${qs(params)}`),
  analytics: (days) => req('GET', `/api/platform/analytics?${qs({ days })}`),
  loginHistory: (params) => req('GET', `/api/platform/login-history?${qs(params)}`),
};

// ── Company Admin ───────────────────────────────────────────────────────────
// `companyId` is optional — only super admins need to pass it; company admins
// are scoped server-side to their own company.
export const admin = {
  stats: (companyId) => req('GET', `/api/admin/stats?${qs({ company_id: companyId })}`),
  agents: (params) => req('GET', `/api/admin/agents?${qs(params)}`),
  createAgent: (b, companyId) => req('POST', `/api/admin/agents?${qs({ company_id: companyId })}`, b),
  updateAgent: (id, b, companyId) => req('PATCH', `/api/admin/agents/${id}?${qs({ company_id: companyId })}`, b),
  activate: (id, companyId) => req('POST', `/api/admin/agents/${id}/activate?${qs({ company_id: companyId })}`),
  deactivate: (id, companyId) => req('POST', `/api/admin/agents/${id}/deactivate?${qs({ company_id: companyId })}`),
  resetPassword: (id, companyId) => req('POST', `/api/admin/agents/${id}/reset-password?${qs({ company_id: companyId })}`),
  audit: (params) => req('GET', `/api/admin/audit?${qs(params)}`),
  loginHistory: (params) => req('GET', `/api/admin/login-history?${qs(params)}`),
  analytics: (days, companyId) => req('GET', `/api/admin/analytics?${qs({ days, company_id: companyId })}`),
  exportUrl: (fmt, companyId) => `/api/admin/export/agents.${fmt}?${qs({ company_id: companyId })}`,
  documents: (companyId) => req('GET', `/api/admin/documents?${qs({ company_id: companyId })}`),
  uploadDocument: (formData, companyId) =>
    req('POST', `/api/admin/documents?${qs({ company_id: companyId })}`, formData, true),
  deleteDocument: (name, companyId) =>
    req('DELETE', `/api/admin/documents/${encodeURIComponent(name)}?${qs({ company_id: companyId })}`),
};

// ── Sessions (slot booking) ────────────────────────────────────────────────
export const sessions = {
  create: (b, companyId) => req('POST', `/api/admin/slots?${qs({ company_id: companyId })}`, b),
  list: (params) => req('GET', `/api/admin/slots?${qs(params)}`),
  update: (id, b, companyId) => req('PATCH', `/api/admin/slots/${id}?${qs({ company_id: companyId })}`, b),
  cancel: (id, companyId) => req('POST', `/api/admin/slots/${id}/cancel?${qs({ company_id: companyId })}`),
  assign: (id, userIds, companyId) =>
    req('POST', `/api/admin/slots/${id}/assign?${qs({ company_id: companyId })}`, { user_ids: userIds }),
  unassign: (id, userId, companyId) =>
    req('POST', `/api/admin/slots/${id}/unassign?${qs({ company_id: companyId })}`, { user_id: userId }),
  roster: (id, companyId) => req('GET', `/api/admin/slots/${id}/roster?${qs({ company_id: companyId })}`),
  report: (id, companyId) => req('GET', `/api/admin/slots/${id}/report?${qs({ company_id: companyId })}`),
  agentReport: (reportId, companyId) =>
    req('GET', `/api/admin/roleplay-reports/${reportId}?${qs({ company_id: companyId })}`),
};
