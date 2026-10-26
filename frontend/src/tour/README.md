# Product tour

Guided "next → next → finish" walkthrough shown to sales agents. Built on
[driver.js](https://driverjs.com) (MIT, ~5 kB gzipped) with a thin app-specific
engine.

| File | What it is |
|---|---|
| `steps.js` | **The tour definition.** One object per step — the only file you touch to change tour content. |
| `engine.js` | driver.js wrapper: page navigation, wait-for-element + silent skip, reduced-motion, ARIA. |
| `tourState.js` | Trigger rule + persistence (server flag `tour_completed` + per-user localStorage mirror). |
| `__tests__/` | Vitest specs for the trigger/persistence logic and the step registry shape. |

Popover styling lives in `src/index.css` under the `.pv-tour` section.

## Adding a new feature to the tour

1. Put a `data-tour` attribute on the element you want spotlighted:

   ```jsx
   <button data-tour="my-feature" …>
   ```

2. Add one object to `TOUR_STEPS` in `steps.js`, in tour order:

   ```js
   {
     id: 'my-feature',                      // unique slug
     target: '[data-tour="my-feature"]',    // omit → centered card (welcome/finish style)
     page: 'loansuite',                     // ONLY if the element lives on a specific page
                                            // (omit for sidebar/topbar — always visible)
     title: 'My Feature',
     body: 'One or two plain sentences: what it does + why an agent cares.',
     side: 'right',                         // optional preferred popover side
   }
   ```

That's it. New sidebar entries in `NAV_ITEMS` already carry
`data-tour="nav-<id>"` automatically — you only add the step object.

If the target can be absent (feature-flagged, slow to render), nothing breaks:
the engine waits up to ~3 s, then skips that step silently.

## Timing rule (agreed July 2026)

- Auto-runs after sign-in **until** the agent finishes it **or** explicitly
  closes/skips it (Esc, ✕, overlay click) — both persist as "done" forever.
- An accidental refresh mid-tour does **not** count — it re-offers next login.
- Always replayable from the **?** button in the top bar.

## Persistence

Server is the source of truth: `users.tour_completed_at` →
`tour_completed` on `/api/auth/me`, set via `POST /api/me/tour-complete`
(idempotent). localStorage (`pv_tour_done_<userId>`) mirrors it per user for an
instant decision on load and as an offline fallback.

## Tests

```bash
cd frontend && npx vitest run
```
