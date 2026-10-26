// ── Product tour — trigger & persistence logic ───────────────────────────────
// Pure functions (storage is injectable) so the auto-start rule is unit-testable.
//
// Agreed rule: the tour auto-runs on sign-in UNTIL the agent either finishes it
// or explicitly skips/closes it — both mark it done forever. An accidental
// refresh mid-tour does NOT count as done, so it re-offers next time.
// Source of truth is the server flag (`user.tour_completed`, survives device
// changes); localStorage mirrors it per-user as an instant/offline cache.

const keyFor = (userId) => `pv_tour_done_${userId || 'anon'}`;

function defaultStorage() {
  try { return window.localStorage; } catch { return null; }
}

export function isTourDoneLocal(userId, storage = defaultStorage()) {
  try { return storage?.getItem(keyFor(userId)) === '1'; } catch { return false; }
}

export function markTourDoneLocal(userId, storage = defaultStorage()) {
  try { storage?.setItem(keyFor(userId), '1'); } catch { /* storage disabled */ }
}

/**
 * Should the tour auto-start for this signed-in user right now?
 * @param {object} user  the /api/auth/me payload (public_dict)
 */
export function shouldAutoStartTour(user, storage = defaultStorage()) {
  if (!user || !user.id) return false;
  if (user.must_reset_pw) return false;          // still inside the reset gate
  if (user.tour_completed) return false;         // server says done (any device)
  if (isTourDoneLocal(user.id, storage)) return false; // done here, server not synced yet
  return true;
}

/** Persist completion: local mirror immediately, server flag best-effort. */
export function persistTourDone(user, storage = defaultStorage()) {
  markTourDoneLocal(user?.id, storage);
  return fetch('/api/me/tour-complete', { method: 'POST' })
    .then((r) => r.json())
    .then((d) => (d && d.success ? d.user : null))
    .catch(() => null); // offline — local mirror still suppresses re-runs on this device
}
