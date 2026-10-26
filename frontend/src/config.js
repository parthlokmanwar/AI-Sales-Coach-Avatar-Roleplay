// ── Backend location (single source of truth) ─────────────────────────────────
//
// DEV (local):   leave VITE_API_URL UNSET. Relative '/api' and '/ws' calls ride
//                the Vite dev-server proxy (vite.config.js) → 127.0.0.1:8003.
//
// PROD (Vercel): set VITE_API_URL to the PUBLIC https URL of the backend, e.g. a
//                cloudflared tunnel like  https://xxxx.trycloudflare.com .
//                A built SPA has NO dev proxy, so every /api + /ws call must be
//                pointed at the real backend. Note also that an https page (which
//                is what Vercel serves) may NOT call http:// or ws:// endpoints —
//                the browser blocks it as mixed content. The backend must be
//                reachable over https/wss, which is exactly what a tunnel gives.
//
// Trailing slashes are trimmed so `${API_BASE}/api/...` never doubles up.
export const API_BASE = (import.meta.env.VITE_API_URL || '').replace(/\/+$/, '');

// WebSocket base — same host as the API, with http→ws / https→wss. Falls back to
// the page's own origin so local dev (and the dev proxy) keeps working unchanged.
export const WS_BASE = (API_BASE || window.location.origin).replace(/^http/, 'ws');

// Resolve a possibly-relative backend media URL (e.g. the avatar-thumb proxy
// path "/api/roleplay/avatar-thumb/xxx") to one the browser can actually load.
// <img>/<video> `src` attributes are fetched directly by the browser and do NOT
// pass through the auth.js fetch() interceptor, so a relative "/api/..." path
// would resolve against the page origin (e.g. Vercel, which serves index.html
// for every path) instead of the backend. Prefixing API_BASE fixes that in prod;
// in dev API_BASE is '' so the path rides the Vite proxy unchanged. Absolute URLs
// (e.g. the CDN thumbnails) are returned as-is.
export function mediaUrl(url) {
  if (API_BASE && typeof url === 'string' && url.startsWith('/api')) {
    return API_BASE + url;
  }
  return url;
}
