import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  fetchEquity,
  fetchFlopStrategy,
  fetchFlopStrategyFromPath,
  fetchMultiwayFlopStrategyFromPath,
  fetchOpeningRange,
  SolveError,
} from './api';

describe('fetchOpeningRange', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('returns the parsed response on success', async () => {
    const payload = { stack_bb: 100, iterations: 1000, elapsed_seconds: 2.5, opening_range: {} };
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(payload) }),
    );

    const result = await fetchOpeningRange(100);
    expect(result).toEqual(payload);
    expect(fetch).toHaveBeenCalledWith('/solve/100', { signal: undefined });
  });

  it('throws SolveError with the server-provided detail on failure', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 422,
        json: () => Promise.resolve({ detail: 'stack_bb must be positive' }),
      }),
    );

    await expect(fetchOpeningRange(-5)).rejects.toThrow(SolveError);
    await expect(fetchOpeningRange(-5)).rejects.toThrow('stack_bb must be positive');
  });

  it('falls back to a generic message when the error body is not JSON', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        json: () => Promise.reject(new Error('not json')),
      }),
    );

    await expect(fetchOpeningRange(100)).rejects.toThrow('Request failed (500)');
  });

  it('appends players and position as query params when given', async () => {
    const payload = {
      stack_bb: 100,
      iterations: 1000,
      elapsed_seconds: 2.5,
      opening_range: {},
      position: 'SB',
      positions: ['BTN', 'SB', 'BB'],
    };
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(payload) }),
    );

    const result = await fetchOpeningRange(100, undefined, { players: 3, position: 'SB' });
    expect(result).toEqual(payload);
    expect(fetch).toHaveBeenCalledWith('/solve/100?players=3&position=SB', { signal: undefined });
  });

  it('omits the query string entirely when no params are given', async () => {
    const payload = {
      stack_bb: 100,
      iterations: 1000,
      elapsed_seconds: 2.5,
      opening_range: {},
      position: 'BTN',
      positions: ['BTN', 'BB'],
    };
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(payload) }),
    );

    await fetchOpeningRange(100);
    expect(fetch).toHaveBeenCalledWith('/solve/100', { signal: undefined });
  });
});

describe('fetchEquity', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('returns the parsed response and builds the query string correctly', async () => {
    const payload = { hand_a: 'AhAd', hand_b: '4h3h', board: '2c7d9h', equity_a: 0.85, equity_b: 0.15 };
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(payload) });
    vi.stubGlobal('fetch', fetchMock);

    const result = await fetchEquity('AhAd', '3h4h', '2c7d9h');
    expect(result).toEqual(payload);
    expect(fetchMock).toHaveBeenCalledWith('/equity?hand_a=AhAd&hand_b=3h4h&board=2c7d9h', { signal: undefined });
  });

  it('sends an empty board param when no board is given', async () => {
    const payload = { hand_a: 'AhAd', hand_b: 'KhKd', board: '', equity_a: 0.82, equity_b: 0.18 };
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(payload) });
    vi.stubGlobal('fetch', fetchMock);

    await fetchEquity('AhAd', 'KhKd', '');
    expect(fetchMock).toHaveBeenCalledWith('/equity?hand_a=AhAd&hand_b=KhKd&board=', { signal: undefined });
  });

  it('throws SolveError with the server-provided detail on failure', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 422,
        json: () => Promise.resolve({ detail: 'hand_a and hand_b share a card' }),
      }),
    );

    await expect(fetchEquity('AhAd', 'AhKd', '')).rejects.toThrow(SolveError);
    await expect(fetchEquity('AhAd', 'AhKd', '')).rejects.toThrow('hand_a and hand_b share a card');
  });
});

describe('fetchFlopStrategy', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('returns the parsed response and builds the query string correctly', async () => {
    const payload = {
      board: 'Jh7d2c',
      pot: 10,
      stack_bb: 40,
      iterations: 1000,
      elapsed_seconds: 2.5,
      strategy: {},
      position: 'OOP',
      positions: ['OOP', 'IP'],
    };
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(payload) });
    vi.stubGlobal('fetch', fetchMock);

    const result = await fetchFlopStrategy('flop', 'Jh7d2c', 10, 40, 'OOP');
    expect(result).toEqual(payload);
    expect(fetchMock).toHaveBeenCalledWith(
      '/solve_flop?board=Jh7d2c&pot=10&stack_bb=40&position=OOP',
      { signal: undefined },
    );
  });

  it('throws SolveError with the server-provided detail on failure', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 422,
        json: () => Promise.resolve({ detail: 'board must have exactly 3 cards for a flop, got 2' }),
      }),
    );

    await expect(fetchFlopStrategy('flop', 'Jh7d', 10, 40, 'OOP')).rejects.toThrow(SolveError);
    await expect(fetchFlopStrategy('flop', 'Jh7d', 10, 40, 'OOP')).rejects.toThrow(
      'board must have exactly 3 cards for a flop, got 2',
    );
  });

  it('dispatches to /solve_flop_turn and /solve_flop_to_river for the other depths', async () => {
    const payload = {
      board: 'Jh7d2c',
      pot: 10,
      stack_bb: 40,
      iterations: 200,
      elapsed_seconds: 25.0,
      strategy: {},
      position: 'OOP',
      positions: ['OOP', 'IP'],
    };
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(payload) });
    vi.stubGlobal('fetch', fetchMock);

    await fetchFlopStrategy('flop_turn', 'Jh7d2c', 10, 40, 'OOP');
    expect(fetchMock).toHaveBeenLastCalledWith(
      '/solve_flop_turn?board=Jh7d2c&pot=10&stack_bb=40&position=OOP',
      { signal: undefined },
    );

    await fetchFlopStrategy('flop_to_river', 'Jh7d2c', 10, 40, 'OOP');
    expect(fetchMock).toHaveBeenLastCalledWith(
      '/solve_flop_to_river?board=Jh7d2c&pot=10&stack_bb=40&position=OOP',
      { signal: undefined },
    );
  });
});

describe('fetchFlopStrategyFromPath', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('POSTs a JSON body and returns the parsed response', async () => {
    const payload = {
      board: 'Jh7d2c',
      canonical_board: 'Jc2d7h',
      action_path: ['raise', 'call_or_check'],
      stack_bb: 100,
      effective_stack_bb: 97.5,
      canonical_stack_bb: 100,
      pot: 5,
      hit: false,
      elapsed_seconds: 17.2,
      strategy: {},
      position: 'BB',
      positions: ['BB', 'BTN'],
    };
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(payload) });
    vi.stubGlobal('fetch', fetchMock);

    const result = await fetchFlopStrategyFromPath(100, ['raise', 'call_or_check'], 'Jh7d2c');
    expect(result).toEqual(payload);
    expect(fetchMock).toHaveBeenCalledWith('/solve_flop_from_path', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ stack_bb: 100, action_path: ['raise', 'call_or_check'], board: 'Jh7d2c', players: 2 }),
      signal: undefined,
    });
  });

  it('throws SolveError with the server-provided detail on failure', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 422,
        json: () => Promise.resolve({ detail: "step 0: 'not_a_kind' is not legal at this node" }),
      }),
    );

    await expect(fetchFlopStrategyFromPath(100, ['not_a_kind'], 'Jh7d2c')).rejects.toThrow(SolveError);
    await expect(fetchFlopStrategyFromPath(100, ['not_a_kind'], 'Jh7d2c')).rejects.toThrow(
      "step 0: 'not_a_kind' is not legal at this node",
    );
  });
});

describe('fetchMultiwayFlopStrategyFromPath', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('POSTs a JSON body (including flop_iterations) and returns the parsed response', async () => {
    const payload = {
      board: 'Jh7d2c',
      action_path: ['call_or_check', 'call_or_check', 'call_or_check'],
      stack_bb: 100,
      effective_stack_bb: 99,
      pot: 3,
      flop_iterations: 200,
      elapsed_seconds: 22.5,
      strategy: {},
      trained: {},
      position: 'SB',
      positions: ['SB', 'BB', 'BTN'],
      players: 3,
    };
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(payload) });
    vi.stubGlobal('fetch', fetchMock);

    const result = await fetchMultiwayFlopStrategyFromPath(
      100,
      ['call_or_check', 'call_or_check', 'call_or_check'],
      'Jh7d2c',
      3,
      200,
    );
    expect(result).toEqual(payload);
    expect(fetchMock).toHaveBeenCalledWith('/solve_flop_multiway_from_path', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        stack_bb: 100,
        action_path: ['call_or_check', 'call_or_check', 'call_or_check'],
        board: 'Jh7d2c',
        players: 3,
        flop_iterations: 200,
      }),
      signal: undefined,
    });
  });

  it('throws SolveError with the server-provided detail on failure', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 422,
        json: () =>
          Promise.resolve({
            detail: 'action_path leaves only 2 live position(s) — use /solve_flop_from_path for a 2-survivor situation',
          }),
      }),
    );

    await expect(
      fetchMultiwayFlopStrategyFromPath(100, ['raise', 'fold', 'call_or_check'], 'Jh7d2c', 3),
    ).rejects.toThrow(SolveError);
  });
});
