import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ActionPathSolver } from './ActionPathSolver';

const ROOT_WALK = {
  stack_bb: 100,
  action_path: [],
  is_terminal: false,
  player_to_act: 'BTN',
  live_positions: ['BTN', 'BB'],
  positions: ['BTN', 'BB'],
  pot: 1.5,
  legal_actions: [
    { kind: 'fold', size: null, to_call: 0.5 },
    { kind: 'call_or_check', size: null, to_call: 0.5 },
    { kind: 'raise', size: 2.5, to_call: null },
    { kind: 'all_in', size: 100, to_call: null },
  ],
};

const AFTER_RAISE_WALK = {
  stack_bb: 100,
  action_path: ['raise'],
  is_terminal: false,
  player_to_act: 'BB',
  live_positions: ['BTN', 'BB'],
  pot: 3.5,
  legal_actions: [
    { kind: 'fold', size: null, to_call: 1.5 },
    { kind: 'call_or_check', size: null, to_call: 1.5 },
    { kind: 'raise', size: 8.5, to_call: null },
    { kind: 'all_in', size: 100, to_call: null },
  ],
};

const OPEN_CALL_TERMINAL_WALK = {
  stack_bb: 100,
  action_path: ['raise', 'call_or_check'],
  is_terminal: true,
  player_to_act: null,
  live_positions: ['BTN', 'BB'],
  pot: 5,
  legal_actions: [],
};

const FOLD_OUT_WALK = {
  stack_bb: 100,
  action_path: ['fold'],
  is_terminal: true,
  player_to_act: null,
  live_positions: ['BB'],
  pot: 1.5,
  legal_actions: [],
};

function solveResponse(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    board: 'Jh7d2c',
    canonical_board: 'Jc2d7h',
    action_path: ['raise', 'call_or_check'],
    stack_bb: 100,
    effective_stack_bb: 97.5,
    canonical_stack_bb: 100,
    pot: 5,
    hit: false,
    elapsed_seconds: 17.2,
    position: 'BB',
    positions: ['BB', 'BTN'],
    strategy: {
      AdAc: { call_or_check: 0.02, 'raise:7.50': 0.6, 'all_in:97.50': 0.38 },
    },
    ...overrides,
  };
}

/** Routes a stubbed fetch by URL: /preflop_walk bodies are handed to
 * `walk(actionPath)`, /solve_flop_from_path calls are handed to `solve`
 * (a zero-arg thunk, since this component's solve request only ever
 * matters for its board/stack/path — no test here needs to branch on it). */
function mockFetch(walk: (actionPath: string[]) => unknown, solve?: () => unknown) {
  return vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    if (url === '/preflop_walk') {
      const body = JSON.parse(init?.body as string) as { action_path: string[] };
      return Promise.resolve({ ok: true, json: () => Promise.resolve(walk(body.action_path)) });
    }
    if (url === '/solve_flop_from_path') {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(solve ? solve() : solveResponse()) });
    }
    throw new Error(`unexpected fetch to ${url}`);
  });
}

function walkFor(path: string[]) {
  if (path.length === 0) return ROOT_WALK;
  if (path.length === 1 && path[0] === 'raise') return AFTER_RAISE_WALK;
  if (path.length === 1 && path[0] === 'fold') return FOLD_OUT_WALK;
  if (path.length === 2 && path[0] === 'raise' && path[1] === 'call_or_check') return OPEN_CALL_TERMINAL_WALK;
  throw new Error(`no fixture walk response for path ${JSON.stringify(path)}`);
}

describe('ActionPathSolver', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('walks the root on mount and renders its legal actions', async () => {
    const fetchMock = mockFetch(walkFor);
    vi.stubGlobal('fetch', fetchMock);

    render(<ActionPathSolver />);

    await waitFor(() => expect(screen.getByRole('button', { name: 'Fold' })).toBeInTheDocument());
    // Root is BTN facing BB's 1.0bb, having posted only the 0.5bb small
    // blind — a real 0.5bb call, not a free check.
    expect(screen.getByRole('button', { name: 'Call 0.5' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Raise to 2.5' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'All-in 100' })).toBeInTheDocument();
    // usePreflopWalk always supplies a real AbortController signal
    // (unlike the direct fetchFlopStrategyFromPath calls below, which
    // are only given one when this component passes one explicitly).
    expect(fetchMock).toHaveBeenCalledWith('/preflop_walk', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ stack_bb: 100, action_path: [], players: 2 }),
      signal: expect.anything(),
    });
  });

  it('clicking a legal action appends it to the path and re-walks', async () => {
    const fetchMock = mockFetch(walkFor);
    vi.stubGlobal('fetch', fetchMock);

    render(<ActionPathSolver />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Raise to 2.5' })).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Raise to 2.5' }));

    await waitFor(() => expect(screen.getByRole('button', { name: 'Call 1.5' })).toBeInTheDocument());
    expect(fetchMock).toHaveBeenLastCalledWith(
      '/preflop_walk',
      expect.objectContaining({ body: JSON.stringify({ stack_bb: 100, action_path: ['raise'], players: 2 }) }),
    );
    expect(screen.getByText('Raise')).toBeInTheDocument(); // the breadcrumb trail
  });

  it('Undo pops the last action and re-walks', async () => {
    const fetchMock = mockFetch(walkFor);
    vi.stubGlobal('fetch', fetchMock);

    render(<ActionPathSolver />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Raise to 2.5' })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Raise to 2.5' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Call 1.5' })).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Undo' }));

    await waitFor(() => expect(screen.getByRole('button', { name: 'Raise to 2.5' })).toBeInTheDocument());
    expect(fetchMock).toHaveBeenLastCalledWith(
      '/preflop_walk',
      expect.objectContaining({ body: JSON.stringify({ stack_bb: 100, action_path: [], players: 2 }) }),
    );
  });

  it('Reset clears the path back to root and re-walks', async () => {
    const fetchMock = mockFetch(walkFor);
    vi.stubGlobal('fetch', fetchMock);

    render(<ActionPathSolver />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Raise to 2.5' })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Raise to 2.5' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Call 1.5' })).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Reset' }));

    await waitFor(() => expect(screen.getByRole('button', { name: 'Raise to 2.5' })).toBeInTheDocument());
    expect(fetchMock).toHaveBeenLastCalledWith(
      '/preflop_walk',
      expect.objectContaining({ body: JSON.stringify({ stack_bb: 100, action_path: [], players: 2 }) }),
    );
  });

  it('Undo and Reset are disabled at the root, where there is nothing to undo', async () => {
    vi.stubGlobal('fetch', mockFetch(walkFor));
    render(<ActionPathSolver />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Fold' })).toBeInTheDocument());

    expect(screen.getByRole('button', { name: 'Undo' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Reset' })).toBeDisabled();
  });

  it('a preset sets the full path in one click and shows the board/solve UI at a real terminal', async () => {
    const fetchMock = mockFetch(walkFor);
    vi.stubGlobal('fetch', fetchMock);

    render(<ActionPathSolver />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Fold' })).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'BTN opens, BB calls' }));

    await waitFor(() => expect(screen.getByLabelText('Board')).toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Solve' })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenLastCalledWith(
      '/preflop_walk',
      expect.objectContaining({
        body: JSON.stringify({ stack_bb: 100, action_path: ['raise', 'call_or_check'], players: 2 }),
      }),
    );
  });

  it('shows a "hand\'s over" message with no board input when the path folds out to one live position', async () => {
    vi.stubGlobal('fetch', mockFetch(walkFor));
    render(<ActionPathSolver />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Fold' })).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Fold' }));

    await waitFor(() => expect(screen.getByText("Hand's over — BB wins the 1.5bb pot.")).toBeInTheDocument());
    expect(screen.queryByLabelText('Board')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Solve' })).not.toBeInTheDocument();
  });

  it('solves and shows each combo once a real terminal is reached, POSTing the right body', async () => {
    vi.stubGlobal('fetch', mockFetch(walkFor));
    render(<ActionPathSolver />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Fold' })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'BTN opens, BB calls' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Solve' })).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Solve' }));

    await waitFor(() => expect(screen.getByText('AdAc')).toBeInTheDocument());
    expect(screen.getByText('Solved live')).toBeInTheDocument();
  });

  it('changing the stack resets the action path back to root', async () => {
    const fetchMock = mockFetch(walkFor);
    vi.stubGlobal('fetch', fetchMock);

    render(<ActionPathSolver />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Raise to 2.5' })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Raise to 2.5' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Call 1.5' })).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText('Stack (bb)'), { target: { value: '50' } });

    await waitFor(() =>
      expect(fetchMock).toHaveBeenLastCalledWith(
        '/preflop_walk',
        expect.objectContaining({ body: JSON.stringify({ stack_bb: 50, action_path: [], players: 2 }) }),
      ),
    );
  });

  it('shows a walk error with no legal-action buttons on a rejected walk', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 422,
        json: () => Promise.resolve({ detail: 'stack_bb must be positive' }),
      }),
    );

    render(<ActionPathSolver />);

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('stack_bb must be positive'));
    expect(screen.queryByRole('button', { name: 'Fold' })).not.toBeInTheDocument();
  });

  it('switching table size resets the path, hides heads-up presets, and re-walks with the new players value', async () => {
    const THREE_MAX_ROOT_WALK = {
      stack_bb: 100,
      action_path: [],
      is_terminal: false,
      player_to_act: 'BTN',
      live_positions: ['BTN', 'SB', 'BB'],
      positions: ['BTN', 'SB', 'BB'],
      pot: 1.5,
      legal_actions: [
        { kind: 'fold', size: null, to_call: 0.5 },
        { kind: 'call_or_check', size: null, to_call: 0.5 },
        { kind: 'raise', size: 2.5, to_call: null },
        { kind: 'all_in', size: 100, to_call: null },
      ],
    };
    const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (url === '/preflop_walk') {
        const body = JSON.parse(init?.body as string) as { action_path: string[]; players: number };
        const response = body.players === 3 ? THREE_MAX_ROOT_WALK : walkFor(body.action_path);
        return Promise.resolve({ ok: true, json: () => Promise.resolve(response) });
      }
      throw new Error(`unexpected fetch to ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<ActionPathSolver />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Raise to 2.5' })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Raise to 2.5' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Call 1.5' })).toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'BTN opens, BB calls' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '3-max' }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenLastCalledWith(
        '/preflop_walk',
        expect.objectContaining({ body: JSON.stringify({ stack_bb: 100, action_path: [], players: 3 }) }),
      ),
    );
    // Reset back to root — the stale 2-position breadcrumb/legal actions
    // from the heads-up walk must not linger under the new table size.
    expect(screen.getByRole('button', { name: 'Raise to 2.5' })).toBeInTheDocument();
    // Heads-up-shaped presets hidden — they don't reach a real terminal
    // (or the same node at all) against a 3-max tree.
    expect(screen.queryByRole('button', { name: 'BTN opens, BB calls' })).not.toBeInTheDocument();
  });

  it('a solve error after a real terminal does not clobber the walk state', async () => {
    // /preflop_walk keeps succeeding via walkFor; only the
    // /solve_flop_from_path branch is made to reject.
    const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (url === '/preflop_walk') {
        const body = JSON.parse(init?.body as string) as { action_path: string[] };
        return Promise.resolve({ ok: true, json: () => Promise.resolve(walkFor(body.action_path)) });
      }
      if (url === '/solve_flop_from_path') {
        return Promise.resolve({ ok: false, status: 422, json: () => Promise.resolve({ detail: 'bad board' }) });
      }
      throw new Error(`unexpected fetch to ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<ActionPathSolver />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Fold' })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'BTN opens, BB calls' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Solve' })).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Solve' }));

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('bad board'));
    // The walk state (board input, Solve button, breadcrumb) must still
    // be intact — a solve-step failure must not reset the wizard.
    expect(screen.getByLabelText('Board')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Solve' })).toBeInTheDocument();
  });
});
