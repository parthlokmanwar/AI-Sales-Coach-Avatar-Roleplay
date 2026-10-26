// Static, bundled thumbnail fallbacks for the RolePlay gallery.
//
// Some Tavus replicas have no CDN still image (custom replicas) or return 403 on
// their `thumbnail_image_url`, and the animated preview VIDEO is proxied through
// the backend — which the browser can't load in production because the ngrok
// free-tier tunnel serves a browser-warning interstitial to any <video>/<img>
// request (those can't send the `ngrok-skip-browser-warning` header that fetch()
// gets via auth.js). Result: those cards render black.
//
// These JPGs (one frame grabbed from each replica's own sample clip) live in
// `frontend/public/avatars/` and are served by Vercel from the SAME origin as the
// page — no tunnel, no Tavus CDN, no 403 — so they load reliably in dev and prod.
// Keyed by Tavus replica_id. Only the replicas whose live thumbnail was broken are
// listed here; everything else keeps using its working CDN image/video.
const STILLS = {
  "rd3ba0f30551": "/avatars/rd3ba0f30551.jpg", // Olivia - Doctor  (Dr. Ananya Reddy) — CDN 403
  "rfe12d8b9597": "/avatars/rfe12d8b9597.jpg", // Nathan - Bookshelf              — CDN 403
  "r5fb46c843a8": "/avatars/r5fb46c843a8.jpg", // Jakey                           — CDN 403
  "r873e4707689": "/avatars/r873e4707689.jpg", // James - Home Office             — CDN 403
  "rdba02690e60": "/avatars/rdba02690e60.jpg", // Vikas (custom)                  — no CDN still
  "r84698ecbd7a": "/avatars/r84698ecbd7a.jpg", // Shailaja (custom)              — no CDN still
  "r7e17b3844d4": "/avatars/r7e17b3844d4.jpg", // Jai (custom, sales practice)   — CDN needs key
};

// Bundled still for a replica_id, or "" if it has a working CDN image already.
export function avatarStill(replicaId) {
  return STILLS[replicaId] || "";
}
