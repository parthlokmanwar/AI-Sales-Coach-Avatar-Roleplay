// ── Product tour — engine ────────────────────────────────────────────────────
// Thin wrapper around driver.js (MIT, ~5 kB) that adds what the app needs:
//   • page navigation between steps (the app has no router — steps declare a
//     `page` and we call MainApp's setActivePage before highlighting)
//   • wait-for-render: after navigating, poll for the target; if it never
//     appears the step is skipped silently instead of breaking the tour
//   • prefers-reduced-motion → no animations, no smooth scroll
//   • dialog ARIA on the popover
// Steps themselves live in ./steps.js — this file never hard-codes a step.

import { driver } from 'driver.js';
import 'driver.js/dist/driver.css';
import { TOUR_STEPS } from './steps';

const prefersReducedMotion = () =>
  typeof window.matchMedia === 'function' &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches;

// Dev-only trace ring, readable as window.__tourLog in the console.
const dbg = (...parts) => {
  if (!import.meta.env.DEV) return;
  try {
    (window.__tourLog ||= []).push(`${Math.round(performance.now())}ms ${parts.join(' ')}`);
  } catch { /* ignore */ }
};

// Resolve when `selector` exists AND takes up layout space (getClientRects —
// offsetParent is null for position:fixed targets like the assistant bubble).
// MutationObserver + setInterval, NOT requestAnimationFrame: rAF stops firing
// in hidden/background tabs, which would wedge the tour mid page-switch if the
// agent tabs away (observers and timers keep running there).
function waitForElement(selector, timeoutMs) {
  return new Promise((resolve) => {
    const started = performance.now();
    let interval = null;
    let observer = null;
    const finish = (value) => {
      if (interval) clearInterval(interval);
      if (observer) observer.disconnect();
      resolve(value);
    };
    const check = () => {
      const el = document.querySelector(selector);
      if (el && el.getClientRects().length > 0) { finish(el); return true; }
      if (performance.now() - started >= timeoutMs) { finish(null); return true; }
      return false;
    };
    if (check()) return;
    observer = new MutationObserver(() => check());
    observer.observe(document.body, { childList: true, subtree: true, attributes: true });
    interval = setInterval(check, 150); // also enforces the timeout in hidden tabs
  });
}

// Walk from `from` in direction `dir` (+1/-1) to the first step whose target
// is (or becomes) visible, navigating pages as steps declare them.
// Returns the step index, or -1 if we ran off either end.
async function resolveStep(steps, from, dir, navigate) {
  for (let i = from; i >= 0 && i < steps.length; i += dir) {
    const step = steps[i];
    if (!step.target) { dbg('resolve: centered', step.id, '->', i); return i; }
    if (step.page) navigate(step.page);
    // Longer grace after a page switch (component mount + data), short otherwise.
    const el = await waitForElement(step.target, step.page ? 3000 : 1200);
    if (el) { dbg('resolve: found', step.id, '->', i); return i; }
    dbg('resolve: MISSING, skipping', step.id, i);
  }
  return -1;
}

let activeTour = null;

/**
 * Start the product tour.
 * @param {function} navigate   MainApp's setActivePage
 * @param {function} onFinish   called once when the tour ends for ANY reason
 *                              (finished or explicitly closed/skipped) — per
 *                              the agreed rule both mark it completed.
 */
export function startProductTour({ navigate, onFinish }) {
  if (activeTour) return; // one at a time

  const steps = TOUR_STEPS;
  const reduced = prefersReducedMotion();

  const tour = driver({
    animate: !reduced,
    smoothScroll: !reduced,
    overlayOpacity: 0.55,
    stagePadding: 6,
    stageRadius: 12,
    allowClose: true,           // X button, Esc and overlay click = skip
    disableActiveInteraction: true,
    popoverClass: 'pv-tour',
    showProgress: true,
    progressText: '{{current}} of {{total}}',
    nextBtnText: 'Next',
    prevBtnText: 'Back',
    doneBtnText: 'Finish',
    steps: steps.map((s) => ({
      element: s.target,        // undefined → driver centers the popover
      popover: { title: s.title, description: s.body, side: s.side },
    })),
    // Overriding these makes ALL advancement async-aware (buttons + arrow keys
    // route through the same handlers), so page switches can settle first.
    onNextClick: () => { advance(+1); },
    onPrevClick: () => { advance(-1); },
    onPopoverRender: (popover) => {
      popover.wrapper.setAttribute('role', 'dialog');
      popover.wrapper.setAttribute('aria-modal', 'true');
      popover.wrapper.setAttribute('aria-label', popover.title.textContent || 'Product tour');
    },
    onDestroyed: () => {
      activeTour = null;
      onFinish?.();
    },
  });

  let advancing = false;
  async function advance(dir) {
    if (advancing) { dbg('advance: ignored (busy)', dir); return; }
    advancing = true;
    try {
      const current = tour.getActiveIndex() ?? 0;
      dbg('advance:', dir, 'from index', current);
      const next = await resolveStep(steps, current + dir, dir, navigate);
      if (next === -1) {
        dbg('advance: ran off end, dir', dir);
        if (dir > 0) tour.destroy(); // walked past the last step → finish
        return;                      // walked before the first → stay put
      }
      dbg('advance: moveTo', next);
      tour.moveTo(next);
    } finally {
      advancing = false;
    }
  }

  activeTour = tour;
  (async () => {
    const first = await resolveStep(steps, 0, +1, navigate);
    if (first === -1) { activeTour = null; onFinish?.(); return; } // nothing renderable
    tour.drive(first);
  })();
}

export function isTourRunning() {
  return activeTour !== null;
}
