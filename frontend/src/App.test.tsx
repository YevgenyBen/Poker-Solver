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
        opening_range: Object.fromEntries(
          ['AA', 'KK', '72o'].map((hand) => [hand, { fold: hand === '72o' ? 0.8 : 0.0, raise: hand === '72o' ? 0.2 : 1.0 }]),
        ),
        position: 'BTN',
        positions: ['BTN', 'BB'],
      }),
  };
}

function mockMultiwayResponseFor(position: string, positions: string[] = ['BTN', 'SB', 'BB']) {
  return {
    ok: true,
    json: () =>
      Promise.resolve({
        stack_bb: 100,
        iterations: 100000,
        elapsed_seconds: 12.5,
        opening_range: Object.fromEntries(['AA', 'KK', '72o'].map((hand) => [hand, { fold: 0.1, raise: 0.9 }])),
        position,
        positions,
      }),
  };
}

// M25: ActionPathSolver's usePreflopWalk fires a POST /preflop_walk on
// mount, unlike every other section of the page (which only fetches on
// an explicit user action) — so every test below that renders <App />
// needs to answer this request too, not just /solve. A fixed root-state
// payload is enough: no test in this file exercises ActionPathSolver's
// own behavior (that's ActionPathSolver.test.tsx's job), so all that
// matters here is that the shape is well-formed and doesn't crash.
function mockWalkResponse() {
  return {
    ok: true,
    json: () =>
      Promise.resolve({
        stack_bb: 100,
        action_path: [],
        is_terminal: false,
        player_to_act: 'BTN',
        live_positions: ['BTN', 'BB'],
        pot: 1.5,
        legal_actions: [
          { kind: 'fold', size: null, to_call: 0.5 },
          { kind: 'call_or_check', size: null, to_call: 0.5 },
          { kind: 'raise', size: 2.5, to_call: null },
          { kind: 'all_in', size: 100, to_call: null },
        ],
      }),
  };
}

describe('App', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('loads the default stack on mount and renders the grid', async () => {
    const fetchMock = vi.fn((url: string) =>
      Promise.resolve(url === '/preflop_walk' ? mockWalkResponse() : mockResponseFor(100)),
    );
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);

    expect(fetchMock).toHaveBeenCalledWith('/solve/100', expect.anything());
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/solved in 2.50s/i));
    expect(screen.getAllByRole('button', { name: /^[AKQJT2-9]/ })).not.toHaveLength(0);
  });

  it('clicking a hand shows its breakdown in the detail panel', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string) => Promise.resolve(url === '/preflop_walk' ? mockWalkResponse() : mockResponseFor(100))),
    );
    const user = userEvent.setup();
    render(<App />);

    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/solved/i));
    await user.click(screen.getByText('72o'));

    expect(screen.getByRole('heading', { name: '72o' })).toBeInTheDocument();
    expect(screen.getByText('80.0%')).toBeInTheDocument();
  });

  it('switching stack depth via a preset re-solves', async () => {
    const fetchMock = vi.fn((url: string) =>
      Promise.resolve(url === '/preflop_walk' ? mockWalkResponse() : mockResponseFor(Number(url.split('/').pop()))),
    );
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();
    render(<App />);

    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/solved/i));
    await user.click(screen.getByRole('button', { name: '50bb' }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/solve/50', expect.anything()));
  });

  it('switching to 3-max mode re-solves with players=3 and reveals the position selector', async () => {
    const fetchMock = vi.fn((url: string) =>
      Promise.resolve(url === '/preflop_walk' ? mockWalkResponse() : mockMultiwayResponseFor('BTN')),
    );
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();
    render(<App />);

    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/solved/i));
    await user.click(screen.getByRole('button', { name: '3-max (demo)' }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenLastCalledWith('/solve/100?players=3&position=BTN', expect.anything()),
    );
    expect(screen.getByRole('button', { name: 'SB' })).toBeInTheDocument();
  });

  it('picking a different position in 3-max mode re-solves for that position', async () => {
    const fetchMock = vi.fn((url: string) => {
      if (url === '/preflop_walk') return Promise.resolve(mockWalkResponse());
      const position = new URL(url, 'http://localhost').searchParams.get('position') ?? 'BTN';
      return Promise.resolve(mockMultiwayResponseFor(position));
    });
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();
    render(<App />);

    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/solved/i));
    await user.click(screen.getByRole('button', { name: '3-max (demo)' }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenLastCalledWith('/solve/100?players=3&position=BTN', expect.anything()),
    );

    await user.click(screen.getByRole('button', { name: 'SB' }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenLastCalledWith('/solve/100?players=3&position=SB', expect.anything()),
    );
  });

  it.each([
    ['6-max (demo)', 6, ['UTG', 'MP', 'CO', 'BTN', 'SB', 'BB']],
    ['9-max (demo)', 9, ['UTG', 'UTG1', 'MP1', 'MP2', 'MP3', 'CO', 'BTN', 'SB', 'BB']],
  ] as const)('switching to %s re-solves with players=%d and shows every position', async (label, players, positions) => {
    const fetchMock = vi.fn((url: string) =>
      Promise.resolve(
        url === '/preflop_walk' ? mockWalkResponse() : mockMultiwayResponseFor(positions[0], [...positions]),
      ),
    );
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();
    render(<App />);

    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/solved/i));
    await user.click(screen.getByRole('button', { name: label }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenLastCalledWith(
        `/solve/100?players=${players}&position=${positions[0]}`,
        expect.anything(),
      ),
    );
    for (const pos of positions) {
      expect(screen.getByRole('button', { name: pos })).toBeInTheDocument();
    }
  });
});
