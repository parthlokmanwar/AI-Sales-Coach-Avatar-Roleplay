// ── Auth client ───────────────────────────────────────────────────────────────
// Token storage + a one-time global fetch() interceptor. Installing the
// interceptor means every existing `fetch('/api/...')` call across the app
// becomes authenticated automatically — no per-call edits required — and any
// 401 from the backend transparently bounces the user back to the sign-in page.

import { API_BASE } from './config';

// ngrok's free tier serves a one-time HTML interstitial ("You are about to visit…")
// to browsers, which would break JSON/API responses. Sending this header on every
// backend request tells ngrok to skip it and pass straight through. It's only added
// when the backend is actually an ngrok URL, so other backends never see it.
const NGROK_BACKEND = /ngrok/i.test(API_BASE);

const TOKEN_KEY = 'pv_auth_token';
const USER_KEY = 'pv_auth_user';

export function getToken() {
  try { return localStorage.getItem(TOKEN_KEY); } catch { return null; }
}

export function setSession(token, user) {
  try {
    localStorage.setItem(TOKEN_KEY, token);
    if (user) localStorage.setItem(USER_KEY, JSON.stringify(user));
  } catch { /* storage disabled — session lives for this tab only */ }
}

export function getStoredUser() {
  try { return JSON.parse(localStorage.getItem(USER_KEY) || 'null'); } catch { return null; }
}

export function setStoredUser(user) {
  try { if (user) localStorage.setItem(USER_KEY, JSON.stringify(user)); } catch { /* ignore */ }
}

export function clearSession() {
  try {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
  } catch { /* ignore */ }
}

function urlOf(input) {
  if (typeof input === 'string') return input;
  if (input instanceof URL) return input.href;
  if (input && typeof input.url === 'string') return input.url; // Request object
  return '';
}

function isApiUrl(url) {
  if (!url) return false;
  return url.startsWith('/api')
    || url.startsWith(`${location.origin}/api`)
    || (!!API_BASE && url.startsWith(`${API_BASE}/api`));   // absolute backend URL (prod)
}

let installed = false;

// Install once, near app startup. `onUnauthorized` is invoked whenever a
// protected API call comes back 401 (expired/invalid session).
export function installAuthFetch(onUnauthorized) {
  if (installed) return;
  installed = true;
  const orig = window.fetch.bind(window);

  window.fetch = async (input, init = {}) => {
    // PROD (Vercel): VITE_API_URL points at the real backend. Re-point any relative
    // /api or /ws path onto it — a built SPA has no Vite dev-proxy. In DEV API_BASE
    // is '' so this is a no-op and the relative path rides the proxy unchanged.
    if (typeof input === 'string' && API_BASE
        && (input.startsWith('/api') || input.startsWith('/ws'))) {
      input = API_BASE + input;
    }
    const url = urlOf(input);
    const api = isApiUrl(url);
    const token = getToken();

    if (api) {
      const headers = new Headers(
        init.headers || (typeof input === 'object' && input?.headers) || {}
      );
      if (token && !headers.has('Authorization')) headers.set('Authorization', `Bearer ${token}`);
      if (NGROK_BACKEND && !headers.has('ngrok-skip-browser-warning')) {
        headers.set('ngrok-skip-browser-warning', 'true');
      }
      init = { ...init, headers };
    }

    const res = await orig(input, init);

    if (res.status === 401 && api && !url.includes('/api/auth/login')) {
      clearSession();
      if (typeof onUnauthorized === 'function') onUnauthorized();
    }
    return res;
  };
}

// Invisible characters a pasted credential may carry that String.trim() does NOT
// remove: zero-width space (0x200B), zero-width non-joiner (0x200C), zero-width
// joiner (0x200D) and BOM / zero-width no-break space (0xFEFF). The set is built
// from numeric code points so this source file contains no invisible characters.
const INVISIBLE = new RegExp('[' + String.fromCharCode(0x200B, 0x200C, 0x200D, 0xFEFF) + ']', 'g');

// Clean a credential before it is sent. A value that looks correct on screen can
// still carry an invisible leading/trailing character picked up when pasted (e.g.
// from a chat message) — a space, tab, newline, non-breaking space, zero-width
// character or BOM — which is enough to fail the backend's byte-for-byte password
// check and show a false "Invalid email or password". String.trim() removes all
// edge Unicode whitespace (including U+00A0); the INVISIBLE pass removes the
// zero-width characters trim() leaves behind. The seeded credentials contain none
// of these, so this only ever strips accidental padding.
function cleanCredential(value) {
  return String(value == null ? '' : value).replace(INVISIBLE, '').trim();
}

// ── API calls ─────────────────────────────────────────────────────────────────

const SERVER_DOWN_MSG =
  'Cannot reach the server. Make sure the backend (and tunnel, if deployed) is running, then try again.';

export async function login(email, password) {
  let res;
  try {
    res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: cleanCredential(email), password: cleanCredential(password) }),
    });
  } catch {
    // fetch itself rejected — DNS failure / connection refused / dead tunnel
    throw new Error(SERVER_DOWN_MSG);
  }
  let data = {};
  try { data = await res.json(); } catch { /* non-JSON error */ }
  if (!res.ok || !data.success) {
    // 5xx with no JSON detail = dev-proxy or gateway answering for a dead
    // backend — that's an outage, not a wrong password.
    if (res.status >= 500 && !data.detail) throw new Error(SERVER_DOWN_MSG);
    throw new Error(data.detail || 'Invalid email or password.');
  }
  setSession(data.token, data.user);
  return data.user;
}

export async function fetchMe() {
  const res = await fetch('/api/auth/me');
  if (!res.ok) return null;
  const data = await res.json().catch(() => ({}));
  return data.success ? data.user : null;
}

export async function logout() {
  try { await fetch('/api/auth/logout', { method: 'POST' }); } catch { /* offline */ }
  clearSession();
}

// ── Account lifecycle (Phase 4) ────────────────────────────────────────────────
async function jsonOrThrow(res) {
  let data = {};
  try { data = await res.json(); } catch { /* empty */ }
  if (!res.ok || data.success === false) throw new Error(data.detail || `Request failed (${res.status})`);
  return data;
}

export async function getProfile() {
  return (await jsonOrThrow(await fetch('/api/me/profile'))).profile;
}

export async function updateProfile(name) {
  const data = await jsonOrThrow(await fetch('/api/me/profile', {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }),
  }));
  if (data.user) {
    try { localStorage.setItem(USER_KEY, JSON.stringify(data.user)); } catch { /* ignore */ }
  }
  return data.user;
}

export async function changePassword(oldPassword, newPassword) {
  return jsonOrThrow(await fetch('/api/me/change-password', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
  }));
}

export async function forgotPassword(email) {
  return jsonOrThrow(await fetch('/api/auth/forgot-password', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email }),
  }));
}

export async function resetPassword(token, newPassword) {
  return jsonOrThrow(await fetch('/api/auth/reset-password', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token, new_password: newPassword }),
  }));
}
