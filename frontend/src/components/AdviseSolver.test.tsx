import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AdviseSolver } from './AdviseSolver';

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
  ],
};

const TERMINAL_WALK = {
  ...ROOT_WALK,
  action_path: ['raise', 'call_or_check'],
  is_terminal: true,
  player_to_act: null,
  legal_actions: [],
};

const FOLD_OUT_WALK = {
  ...ROOT_WALK,
  action_path: ['fold'],
  is_terminal: true,
  player_to_act: null,
  live_positions: ['BB'],
  legal_actions: [],
};

function adviceResponse(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    street: 'preflop',
    players: 2,
    positions: ['BTN', 'BB'],
    position: 'BTN',
    player_to_act: 'BTN',
    is_terminal: false,
    pot: 1.5,
    effective_stack_bb: 99,
    strategy: { AKs: { fold: 0.0, call_or_check: 0.3, 'raise:2.50': 0.7 } },
    trained: { AKs: true },
    hero: null,
    source: 'preflop',
    solve_iterations: 1000,
    elapsed_seconds: 1.2,
    range_confidence: null,
    ...overrides,
  };
}

function mockFetch(walkFor: (path: string[]) => unknown, advice?: () => unknown) {
  return vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    if (url === '/preflop_walk') {
      const body = JSON.parse(init?.body as string) as { action_path: string[] };
      return Promise.resolve({ ok: true, json: () => Promise.resolve(walkFor(body.action_path)) });
    }
    if (url === '/advise') {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(advice ? advice() : adviceResponse()) });
    }
    throw new Error(`unexpected fetch to ${url}`);
  });
}

const walkFor = (path: string[]) => {
  if (path.length === 0) return ROOT_WALK;
  if (path.length === 1 && path[0] === 'fold') return FOLD_OUT_WALK;
  if (path.length === 2) return TERMINAL_WALK;
  return ROOT_WALK;
};

describe('AdviseSolver', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('walks the preflop root on mount and offers its legal actions', async () => {
    vi.stubGlobal('fetch', mockFetch(walkFor));
    render(<AdviseSolver />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Raise to 2.5' })).toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Call 0.5' })).toBeInTheDocument();
  });

  it('posts a preflop request with no board when the street is preflop', async () => {
    const fetchMock = mockFetch(walkFor);
    vi.stubGlobal('fetch', fetchMock);
    render(<AdviseSolver />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Get advice' })).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Get advice' }));

    await waitFor(() => expect(screen.getByText('AKs')).toBeInTheDocument());
    const body = JSON.parse(
      (fetchMock.mock.calls.find((c) => c[0] === '/advise')![1] as RequestInit).body as string,
    );
    // Street inference: a preflop query carries no board and no later
    // street's fields at all.
    expect(body.board).toBeUndefined();
    expect(body.turn_card).toBeUndefined();
    expect(body.preflop_action_path).toEqual([]);
  });

  it('includes hero_cards when supplied and renders that hand"s own advice', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetch(walkFor, () =>
        adviceResponse({
          hero: {
            cards: 'AsKs',
            in_range: true,
            strategy: { fold: 0.0, call_or_check: 0.3, 'raise:2.50': 0.7 },
            trained: true,
            range_trained: true,
          },
        }),
      ),
    );
    render(<AdviseSolver />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Get advice' })).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText('Your cards'), { target: { value: 'AsKs' } });
    fireEvent.click(screen.getByRole('button', { name: 'Get advice' }));

    await waitFor(() => expect(screen.getByText('Your hand: AsKs')).toBeInTheDocument());
  });

  it('warns when hero was force-included rather than genuinely in range', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetch(walkFor, () =>
        adviceResponse({
          hero: {
            cards: '7c2d',
            in_range: false,
            strategy: { fold: 0.9, call_or_check: 0.1 },
            trained: true,
            range_trained: true,
          },
        }),
      ),
    );
    render(<AdviseSolver />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Get advice' })).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText('Your cards'), { target: { value: '7c2d' } });
    fireEvent.click(screen.getByRole('button', { name: 'Get advice' }));

    await waitFor(() => expect(screen.getByText(/wasn.t in the top of the derived range/)).toBeInTheDocument());
  });

  it('reveals board and turn inputs for a turn query, and posts them', async () => {
    const fetchMock = mockFetch(walkFor, () => adviceResponse({ street: 'turn', source: 'exact' }));
    vi.stubGlobal('fetch', fetchMock);
    render(<AdviseSolver />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Raise to 2.5' })).toBeInTheDocument());

    // Close the preflop action so a postflop street is available.
    fireEvent.click(screen.getByRole('button', { name: 'Raise to 2.5' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Call 0.5' })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Call 0.5' }));

    fireEvent.click(screen.getByRole('button', { name: 'Turn' }));
    await waitFor(() => expect(screen.getByLabelText('Turn card')).toBeInTheDocument());
    expect(screen.getByLabelText('Board')).toBeInTheDocument();
    expect(screen.getByLabelText('Flop line')).toBeInTheDocument();
    // River-only inputs stay hidden at turn depth.
    expect(screen.queryByLabelText('River card')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Get advice' }));
    await waitFor(() => expect(screen.getByText('AKs')).toBeInTheDocument());
    const body = JSON.parse(
      (fetchMock.mock.calls.filter((c) => c[0] === '/advise').pop()![1] as RequestInit).body as string,
    );
    expect(body.board).toBe('Jh7d2c');
    expect(body.turn_card).toBe('Ts');
    expect(body.flop_action_path).toEqual(['call_or_check', 'call_or_check']);
    expect(body.river_card).toBeUndefined();
  });

  it('shows a null-trained explanation for a cached answer', async () => {
    vi.stubGlobal('fetch', mockFetch(walkFor, () => adviceResponse({ source: 'library_hit', trained: null })));
    render(<AdviseSolver />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Get advice' })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Get advice' }));

    await waitFor(() => expect(screen.getByText('Cache hit')).toBeInTheDocument());
    expect(screen.getByText(/Per-hand confidence isn.t available/)).toBeInTheDocument();
  });

  it('surfaces range confidence, flagging a partly untrained range', async () => {
    vi.stubGlobal(
      'fetch',
      mockFetch(walkFor, () =>
        adviceResponse({
          range_confidence: {
            BTN: { trained_classes: 0, total_classes: 3, fully_trained: false },
            BB: { trained_classes: 3, total_classes: 3, fully_trained: true },
          },
        }),
      ),
    );
    render(<AdviseSolver />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Get advice' })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Get advice' }));

    await waitFor(() => expect(screen.getByText(/BTN 0\/3/)).toBeInTheDocument());
    expect(screen.getByText(/untrained default/)).toBeInTheDocument();
  });

  it("warns when hero's own hand was never solved for", async () => {
    // M83: hero.trained was computed all along and never rendered, so a
    // uniform placeholder strategy displayed exactly like a real one.
    // This is the most direct honesty signal in the product — it is about
    // YOUR hand, not the range around it.
    vi.stubGlobal(
      'fetch',
      mockFetch(walkFor, () =>
        adviceResponse({
          hero: {
            cards: 'AsKs',
            in_range: true,
            strategy: { fold: 0.333, call_or_check: 0.333, 'raise:2.50': 0.334 },
            trained: false,
            range_trained: null,
          },
        }),
      ),
    );
    render(<AdviseSolver />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Get advice' })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Get advice' }));

    const warning = await screen.findByRole('alert');
    expect(warning).toHaveTextContent('Not solved for your hand');
  });

  it('does not claim the hand is over when there is simply no advice for it', async () => {
    // M83: a null hero strategy used to render "the hand resolved before
    // this street" regardless of whether it had. At a LIVE node that is
    // false — there IS a decision, we just have nothing for this hand.
    vi.stubGlobal(
      'fetch',
      mockFetch(walkFor, () =>
        adviceResponse({
          is_terminal: false,
          hero: { cards: 'AsKs', in_range: false, strategy: null, trained: null, range_trained: null },
        }),
      ),
    );
    render(<AdviseSolver />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Get advice' })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Get advice' }));

    const warning = await screen.findByRole('alert');
    expect(warning).toHaveTextContent('No advice for this hand');
    expect(screen.queryByText(/hand resolved before this street/)).not.toBeInTheDocument();
  });

  it('still says the hand resolved when it genuinely did', async () => {
    // The other half — the honest message must survive for the case it
    // was written for, or this fix just trades one wrong message for
    // another.
    vi.stubGlobal(
      'fetch',
      mockFetch(walkFor, () =>
        adviceResponse({
          is_terminal: true,
          hero: { cards: 'AsKs', in_range: true, strategy: null, trained: null, range_trained: null },
        }),
      ),
    );
    render(<AdviseSolver />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Get advice' })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Get advice' }));

    await waitFor(() =>
      expect(screen.getByText(/hand resolved before this street/)).toBeInTheDocument(),
    );
  });

  it('sends a partial action path for the decision the player faces', async () => {
    // M94: /advise has answered any decision on any street since M84-M89,
    // and the UI could still only ask about each street's OPENING one —
    // capability shipped in the API that no user could reach. The same
    // pattern as M82's unrendered solver_confidence, repeated four
    // milestones later, which is why this asserts the REQUEST rather than
    // just that a control renders.
    // Built on the file's own mockFetch so the walk fixtures (and so the
    // button labels) match every other test here; only the /advise body
    // is captured on top.
    const sent: Record<string, unknown>[] = [];
    const base = mockFetch(walkFor, () => adviceResponse({ street: 'flop' }));
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url === '/advise') sent.push(JSON.parse(String(init?.body)));
        return base(url, init);
      }),
    );

    render(<AdviseSolver />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Raise to 2.5' })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Raise to 2.5' }));
    await waitFor(() => expect(screen.getByRole('button', { name: /^Call/ })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /^Call/ }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Flop' })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Flop' }));

    const spot = await screen.findByLabelText('Your spot');
    fireEvent.change(spot, { target: { value: 'facing_bet' } });
    fireEvent.click(screen.getByRole('button', { name: 'Get advice' }));

    await waitFor(() => expect(sent.length).toBeGreaterThan(0));
    const body = sent[sent.length - 1];
    // The partial path is what reaches the facing-a-bet node. It must NOT
    // close the street, or there would be no decision left on it.
    expect(body.flop_action_path).toEqual(['raise']);
  });

  it('omits the action path when the player is first to act', async () => {
    // The other half: "first to act" must send NO path, because an absent
    // path already means the street's opening decision. Sending an empty
    // or partial one instead would be a different question.
    // Built on the file's own mockFetch so the walk fixtures (and so the
    // button labels) match every other test here; only the /advise body
    // is captured on top.
    const sent: Record<string, unknown>[] = [];
    const base = mockFetch(walkFor, () => adviceResponse({ street: 'flop' }));
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url === '/advise') sent.push(JSON.parse(String(init?.body)));
        return base(url, init);
      }),
    );

    render(<AdviseSolver />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Raise to 2.5' })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Raise to 2.5' }));
    await waitFor(() => expect(screen.getByRole('button', { name: /^Call/ })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /^Call/ }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Flop' })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Flop' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Get advice' }));

    await waitFor(() => expect(sent.length).toBeGreaterThan(0));
    expect(sent[sent.length - 1].flop_action_path).toBeUndefined();
  });

  it('warns loudly when the backend reports low solver confidence', async () => {
    // M82: the backend has been able to say "this table size does not
    // converge" since M76, but nothing in the UI read it — a 9-max user
    // saw advice with no sign it is known-unreliable. That is the one
    // failure mode this project's honesty signals exist to prevent, so
    // the warning is pinned here rather than left to survive by luck.
    vi.stubGlobal(
      'fetch',
      mockFetch(walkFor, () =>
        adviceResponse({
          players: 9,
          solver_confidence: 'low',
          solver_confidence_reason: '9-max preflop does not converge at any affordable budget.',
        }),
      ),
    );
    render(<AdviseSolver />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Get advice' })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Get advice' }));

    const warning = await screen.findByRole('alert');
    expect(warning).toHaveTextContent('Low confidence');
    // The REASON must reach the user too, not just the label — a bare
    // "low confidence" tells them nothing actionable.
    expect(warning).toHaveTextContent(/does not converge/);
  });

  it('shows no confidence warning when the solver is trusted', async () => {
    // The other half: the warning must not be permanent furniture, or
    // users learn to ignore it.
    vi.stubGlobal('fetch', mockFetch(walkFor, () => adviceResponse({ solver_confidence: 'high' })));
    render(<AdviseSolver />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Get advice' })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Get advice' }));

    await waitFor(() => expect(screen.getByText('AKs')).toBeInTheDocument());
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it("says the hand's over when the preflop path folds everyone out", async () => {
    vi.stubGlobal('fetch', mockFetch(walkFor));
    render(<AdviseSolver />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Fold' })).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Fold' }));

    await waitFor(() => expect(screen.getByText(/Hand.s over/)).toBeInTheDocument());
    expect(screen.queryByRole('button', { name: 'Get advice' })).not.toBeInTheDocument();
  });

  it('explains that a closed preflop action has no preflop decision left', async () => {
    vi.stubGlobal('fetch', mockFetch(walkFor));
    render(<AdviseSolver />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Raise to 2.5' })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Raise to 2.5' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Call 0.5' })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Call 0.5' }));

    await waitFor(() => expect(screen.getByText(/no preflop decision left to advise/)).toBeInTheDocument());
  });

  it('surfaces a server error and clears any stale result', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((url: string, init?: RequestInit) => {
        if (url === '/preflop_walk') {
          const body = JSON.parse(init?.body as string) as { action_path: string[] };
          return Promise.resolve({ ok: true, json: () => Promise.resolve(walkFor(body.action_path)) });
        }
        return Promise.resolve({
          ok: false,
          status: 422,
          json: () => Promise.resolve({ detail: 'board must have exactly 3 cards for a flop, got 2' }),
        });
      }),
    );
    render(<AdviseSolver />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Get advice' })).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Get advice' }));

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent('board must have exactly 3 cards for a flop, got 2'),
    );
    expect(screen.queryByText('AKs')).not.toBeInTheDocument();
  });
});
