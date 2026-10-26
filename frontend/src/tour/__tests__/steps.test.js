import { describe, it, expect } from 'vitest';
import { TOUR_STEPS } from '../steps';

// Pages that exist in MainApp's renderPage() switch (App.jsx). If a page id is
// renamed there, this list — and any step pointing at it — must follow.
const KNOWN_PAGES = ['home', 'roleplay', 'generator', 'askme', 'loansuite', 'history'];

describe('TOUR_STEPS registry', () => {
  it('has unique ids', () => {
    const ids = TOUR_STEPS.map((s) => s.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it('every step has an id, title and body', () => {
    for (const s of TOUR_STEPS) {
      expect(s.id, JSON.stringify(s)).toBeTruthy();
      expect(s.title, s.id).toBeTruthy();
      expect(s.body, s.id).toBeTruthy();
    }
  });

  it('targets are data-tour selectors (the stable contract with the DOM)', () => {
    for (const s of TOUR_STEPS.filter((x) => x.target)) {
      expect(s.target, s.id).toMatch(/^\[data-tour="[a-z0-9-]+"\]$/);
    }
  });

  it('page references point at real pages', () => {
    for (const s of TOUR_STEPS.filter((x) => x.page)) {
      expect(KNOWN_PAGES, `${s.id} → ${s.page}`).toContain(s.page);
    }
  });

  it('covers every product area the tour promises', () => {
    const ids = new Set(TOUR_STEPS.map((s) => s.id));
    for (const required of [
      'nav-home', 'nav-roleplay', 'nav-generator', 'nav-askme',
      'nav-loansuite', 'nav-history',
      'chat-language', 'chat-learnings', 'chat-new',
    ]) {
      expect(ids.has(required), required).toBe(true);
    }
  });

  it('starts with a welcome card and ends with a finish step', () => {
    expect(TOUR_STEPS[0].id).toBe('welcome');
    expect(TOUR_STEPS[0].target).toBeUndefined(); // centered
    expect(TOUR_STEPS[TOUR_STEPS.length - 1].id).toBe('finish');
  });
});
