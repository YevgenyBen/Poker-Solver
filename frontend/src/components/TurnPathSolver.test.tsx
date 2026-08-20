import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { TurnPathSolver } from './TurnPathSolver';

const ROOT_WALK = {
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

function walkFor(path: string[]) {
  if (path.length === 0) return ROOT_WALK;
  if (path.length === 1 && path[0] === 'fold') return FOLD_OUT_WALK;
  if (path.length === 2 && path[0] === 'raise' && path[1] === 'call_or_check') return OPEN_CALL_TERMINAL_WALK;
  throw new Error(`no fixture walk response for path ${JSON.stringify(path)}`);
}

function turnSolveResponse(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    board: 'Jh7d2c',
    turn_card: '2h',
    preflop_action_path: ['raise', 'call_or_check'],
    flop_action_path: ['raise', 'call_or_check'],
    stack_bb: 100,
    effective_stack_bb: 85,
    pot: 30,
    is_terminal: false,
    player_to_act: 'BB',
    strategy: {
      AdAc: { call_or_check: 0.03, 'raise:25.00': 0.23, 'all_in:85.00': 0.74 },
    },
    position: 'BB',
    positions: ['BB', 'BTN'],
    elapsed_seconds: 45.9,
    ...overrides,
  };
}

/** Routes a stubbed fetch by URL, mirroring ActionPathSolver.test.tsx's
 * own mockFetch helper. */
function mockFetch(walk: (path: string[]) => unknown, solve?: () => unknown) {
  return vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    if (url === '/preflop_walk') {
      const body = JSON.parse(init?.body as string) as { action_path: string[] };
      return Promise.resolve({ ok: true, json: () => Promise.resolve(walk(body.action_path)) });
    }
    if (url === '/solve_turn_from_path') {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(solve ? solve() : turnSolveResponse()) });
    }
    throw new Error(`unexpected fetch to ${url}`);
  });
}

describe('TurnPathSolver', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('walks the preflop root on mount and renders its legal actions', async () => {
    vi.stubGlobal('fetch', mockFetch(walkFor));
    render(<TurnPathSolver />);

    await waitFor(() => expect(screen.getByRole('button', { name: 'Fold' })).toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Call 0.5' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Raise to 2.5' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'All-in 100' })).toBeInTheDocument();
    // No flop/turn inputs yet — the preflop leg hasn't reached a terminal.
    expect(screen.queryByLabelText('Board')).not.toBeInTheDocument();
  });

  it('reaching a real preflop terminal shows board/flop-line/turn-card inputs', async () => {
    vi.stubGlobal('fetch', mockFetch(walkFor));
    render(<TurnPathSolver />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'BTN opens, BB calls' })).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'BTN opens, BB calls' }));

    await waitFor(() => expect(screen.getByLabelText('Board')).toBeInTheDocument());
    expect(screen.getByLabelText('Flop line')).toBeInTheDocument();
    expect(screen.getByLabelText('Turn card')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Solve' })).toBeInTheDocument();
  });

  it('solves and shows a real turn strategy, POSTing the right body', async () => {
    const fetchMock = mockFetch(walkFor);
    vi.stubGlobal('fetch', fetchMock);
    render(<TurnPathSolver />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'BTN opens, BB calls' })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'BTN opens, BB calls' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Solve' })).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Solve' }));

    await waitFor(() => expect(screen.getByText('AdAc')).toBeInTheDocument());
    expect(fetchMock).toHaveBeenLastCalledWith(
      '/solve_turn_from_path',
      expect.objectContaining({
        body: JSON.stringify({
          stack_bb: 100,
          preflop_action_path: ['raise', 'call_or_check'],
          board: 'Jh7d2c',
          flop_action_path: ['raise', 'call_or_check'], // the default flop preset, "Bet, call"
          turn_card: '2h',
        }),
      }),
    );
  });

  it('shows a distinct message when the chosen flop line folds out', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetch(walkFor, () => turnSolveResponse({ is_terminal: true, player_to_act: null, strategy: {} })),
    );
    render(<TurnPathSolver />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'BTN opens, BB calls' })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'BTN opens, BB calls' }));
    await waitFor(() => expect(screen.getByLabelText('Flop line')).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText('Flop line'), { target: { value: 'bet_fold' } });
    fireEvent.click(screen.getByRole('button', { name: 'Solve' }));

    await waitFor(() => expect(screen.getByText('Folded on the flop — no turn decision to make.')).toBeInTheDocument());
    expect(screen.queryByText('AdAc')).not.toBeInTheDocument();
  });

  it('shows a distinct message when the flop line is already all in', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetch(walkFor, () =>
        turnSolveResponse({ is_terminal: true, player_to_act: null, strategy: {}, effective_stack_bb: 0 }),
      ),
    );
    render(<TurnPathSolver />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'BTN opens, BB calls' })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'BTN opens, BB calls' }));
    await waitFor(() => expect(screen.getByLabelText('Flop line')).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText('Flop line'), { target: { value: 'allin_call' } });
    fireEvent.click(screen.getByRole('button', { name: 'Solve' }));

    await waitFor(() =>
      expect(screen.getByText('Already all in on the flop — no more decisions, just a showdown.')).toBeInTheDocument(),
    );
  });

  it('shows a "hand\'s over" message with no board input when the preflop path folds out', async () => {
    vi.stubGlobal('fetch', mockFetch(walkFor));
    render(<TurnPathSolver />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Fold' })).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Fold' }));

    await waitFor(() => expect(screen.getByText("Hand's over preflop — BB wins the 1.5bb pot.")).toBeInTheDocument());
    expect(screen.queryByLabelText('Board')).not.toBeInTheDocument();
  });

  it('a walk error surfaces without any legal-action buttons', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 422,
        json: () => Promise.resolve({ detail: 'stack_bb must be positive' }),
      }),
    );

    render(<TurnPathSolver />);

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('stack_bb must be positive'));
    expect(screen.queryByRole('button', { name: 'Fold' })).not.toBeInTheDocument();
  });

  it('a solve error after a real terminal does not clobber the walk state', async () => {
    const fetchMock = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      if (url === '/preflop_walk') {
        const body = JSON.parse(init?.body as string) as { action_path: string[] };
        return Promise.resolve({ ok: true, json: () => Promise.resolve(walkFor(body.action_path)) });
      }
      if (url === '/solve_turn_from_path') {
        return Promise.resolve({ ok: false, status: 422, json: () => Promise.resolve({ detail: 'bad turn card' }) });
      }
      throw new Error(`unexpected fetch to ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<TurnPathSolver />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'BTN opens, BB calls' })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'BTN opens, BB calls' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Solve' })).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Solve' }));

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('bad turn card'));
    expect(screen.getByLabelText('Board')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Solve' })).toBeInTheDocument();
  });
});
