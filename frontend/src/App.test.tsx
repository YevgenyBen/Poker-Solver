import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { App } from './App';

function mockResponseFor(stackBb: number) {
  return {
    ok: true,
    json: () =>
      Promise.resolve({
        stack_bb: stackBb,
        iterations: 1000,
        elapsed_seconds: 2.5,
        opening_range: Object.fromEntries(['AA', 'KK', '72o'].map((hand) => [hand, { fold: 0, raise: 1 }])),
        position: 'BTN',
        positions: ['BTN', 'BB'],
      }),
  };
}

// App.tsx is now just the tab shell — each tab's own real behavior is
// covered by that tab's own dedicated test file (PreflopRangesPage.
// test.tsx, EquityCalculator.test.tsx, FlopSolver.test.tsx,
// CachedFlopSolver.test.tsx, ActionPathSolver.test.tsx). This file only
// tests tab switching, unmounting, and hash-URL sync.
describe('App', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.location.hash = '';
  });

  it('defaults to the Preflop Ranges tab and solves on mount', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockResponseFor(100)));

    render(<App />);

    expect(screen.getByRole('tab', { name: 'Preflop Ranges' })).toHaveAttribute('aria-selected', 'true');
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/solved/i));
  });

  it('switching to another tab shows its content, unmounts the previous tab, and updates the URL hash', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockResponseFor(100)));
    const user = userEvent.setup();
    render(<App />);
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/solved/i));

    await user.click(screen.getByRole('tab', { name: 'Equity Calculator' }));

    expect(screen.getByRole('heading', { name: 'Equity calculator' })).toBeInTheDocument();
    // Real unmount, not just CSS-hidden — the Preflop tab's own input
    // must be gone from the DOM entirely.
    expect(screen.queryByLabelText('Effective stack')).not.toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Equity Calculator' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: 'Preflop Ranges' })).toHaveAttribute('aria-selected', 'false');
    expect(window.location.hash).toBe('#equity');
  });

  it('opens directly to the tab named in the URL hash on load, with no click needed', () => {
    window.location.hash = '#action-path';

    render(<App />);

    expect(screen.getByRole('heading', { name: 'Action-path flop solver' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Action-Path Wizard' })).toHaveAttribute('aria-selected', 'true');
  });

  it('falls back to the default tab for an unknown hash', () => {
    window.location.hash = '#not-a-real-tab';
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockResponseFor(100)));

    render(<App />);

    expect(screen.getByRole('tab', { name: 'Preflop Ranges' })).toHaveAttribute('aria-selected', 'true');
  });

  it('switching away from and back to Preflop Ranges re-solves rather than showing stale content', async () => {
    // This is the behavioral contract the unmount-on-switch design
    // rests on (see useHashRoute.ts/App.tsx's own comments) — a later
    // switch to keep-mounted-but-hidden would fail this loudly instead
    // of silently reintroducing the eager-fetch-on-load problem the
    // tab navigation change fixes.
    const fetchMock = vi.fn().mockResolvedValue(mockResponseFor(100));
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();
    render(<App />);
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/solved/i));
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole('tab', { name: 'Equity Calculator' }));
    expect(screen.queryByRole('status')).not.toBeInTheDocument();

    await user.click(screen.getByRole('tab', { name: 'Preflop Ranges' }));
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/solved/i));

    // The mock resolves fast enough that catching the transient
    // "Solving…" text is racy — a second real fetch call is the robust
    // proof that remounting re-solved instead of showing stale content
    // (there's nowhere for stale content to have been cached in; the
    // whole point is that PreflopRangesPage was unmounted and lost its
    // state, so this can only pass by actually re-fetching).
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
