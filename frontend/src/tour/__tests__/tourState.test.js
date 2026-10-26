import { describe, it, expect } from 'vitest';
import { shouldAutoStartTour, isTourDoneLocal, markTourDoneLocal } from '../tourState';

// Minimal localStorage stand-in so the pure logic runs under node.
function memStorage() {
  const m = new Map();
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
  };
}

const agent = (extra = {}) => ({
  id: 'u1', email: 'a@b.c', role: 'agent',
  must_reset_pw: false, tour_completed: false, ...extra,
});

describe('shouldAutoStartTour', () => {
  it('starts for a fresh agent who has never seen the tour', () => {
    expect(shouldAutoStartTour(agent(), memStorage())).toBe(true);
  });

  it('never starts without a signed-in user', () => {
    const s = memStorage();
    expect(shouldAutoStartTour(null, s)).toBe(false);
    expect(shouldAutoStartTour({}, s)).toBe(false); // no id
  });

  it('waits until the forced password reset is done', () => {
    expect(shouldAutoStartTour(agent({ must_reset_pw: true }), memStorage())).toBe(false);
  });

  it('respects the server flag (completed on another device)', () => {
    expect(shouldAutoStartTour(agent({ tour_completed: true }), memStorage())).toBe(false);
  });

  it('respects the local mirror when the server flag has not synced yet', () => {
    const s = memStorage();
    markTourDoneLocal('u1', s);
    expect(shouldAutoStartTour(agent(), s)).toBe(false);
  });

  it('local mirror is per user — another agent on the same machine still gets the tour', () => {
    const s = memStorage();
    markTourDoneLocal('u1', s);
    expect(shouldAutoStartTour(agent({ id: 'u2' }), s)).toBe(true);
  });

  it('re-offers after an interrupted run (nothing was persisted)', () => {
    // Simulates refresh mid-tour: neither markTourDoneLocal nor the server
    // flag was set, so the next sign-in auto-starts again (agreed rule "c").
    expect(shouldAutoStartTour(agent(), memStorage())).toBe(true);
  });
});

describe('local mirror', () => {
  it('round-trips per user id', () => {
    const s = memStorage();
    expect(isTourDoneLocal('u1', s)).toBe(false);
    markTourDoneLocal('u1', s);
    expect(isTourDoneLocal('u1', s)).toBe(true);
    expect(isTourDoneLocal('u2', s)).toBe(false);
  });

  it('survives a broken storage object without throwing', () => {
    const broken = { getItem: () => { throw new Error('nope'); }, setItem: () => { throw new Error('nope'); } };
    expect(() => markTourDoneLocal('u1', broken)).not.toThrow();
    expect(isTourDoneLocal('u1', broken)).toBe(false);
  });
});
