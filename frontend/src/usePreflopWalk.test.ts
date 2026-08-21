import { renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { usePreflopWalk } from './usePreflopWalk';

function walkPayload(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    stack_bb: 100,
    action_path: [],
    is_terminal: false,
    player_to_act: 'BTN',
    live_positions: ['BTN', 'BB'],
    positions: ['BTN', 'BB'],
    pot: 1.5,
    legal_actions: [{ kind: 'fold', size: null, to_call: 0.5 }],
    ...overrides,
  };
}

describe('usePreflopWalk', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('fetches on mount and returns the walk state', async () => {
    const payload = walkPayload();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(payload) }));

    const { result } = renderHook(() => usePreflopWalk(100, []));

    expect(result.current.loading).toBe(true);

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toEqual(payload);
    expect(result.current.error).toBe('');
  });

  it('POSTs stack_bb, action_path, and players (defaulted to 2) in the request body', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(walkPayload()) });
    vi.stubGlobal('fetch', fetchMock);

    renderHook(() => usePreflopWalk(100, ['raise', 'call_or_check']));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith('/preflop_walk', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ stack_bb: 100, action_path: ['raise', 'call_or_check'], players: 2 }),
        signal: expect.anything(),
      }),
    );
  });

  it('POSTs an explicit players value when given one', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(walkPayload()) });
    vi.stubGlobal('fetch', fetchMock);

    renderHook(() => usePreflopWalk(100, [], 6));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/preflop_walk',
        expect.objectContaining({ body: JSON.stringify({ stack_bb: 100, action_path: [], players: 6 }) }),
      ),
    );
  });

  it('re-fetches when players changes but stackBb and actionPath do not', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(walkPayload()) });
    vi.stubGlobal('fetch', fetchMock);

    const { result, rerender } = renderHook(({ players }) => usePreflopWalk(100, [], players), {
      initialProps: { players: 2 },
    });
    await waitFor(() => expect(result.current.loading).toBe(false));

    rerender({ players: 3 });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(fetchMock).toHaveBeenLastCalledWith(
      '/preflop_walk',
      expect.objectContaining({ body: JSON.stringify({ stack_bb: 100, action_path: [], players: 3 }) }),
    );
  });

  it('surfaces a SolveError message distinctly from a generic failure', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 422,
        json: () => Promise.resolve({ detail: "step 0: 'raise' is not legal at this node" }),
      }),
    );

    const { result } = renderHook(() => usePreflopWalk(100, ['raise']));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe("step 0: 'raise' is not legal at this node");
    expect(result.current.data).toBeNull();
  });

  it('falls back to a generic message when the failure is not a SolveError', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('network down')));

    const { result } = renderHook(() => usePreflopWalk(100, []));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toBe('Something went wrong');
    expect(result.current.data).toBeNull();
  });

  it('re-fetches when actionPath changes but stackBb does not', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(walkPayload()) });
    vi.stubGlobal('fetch', fetchMock);

    const { result, rerender } = renderHook(({ actionPath }) => usePreflopWalk(100, actionPath), {
      initialProps: { actionPath: [] as string[] },
    });
    await waitFor(() => expect(result.current.loading).toBe(false));

    rerender({ actionPath: ['raise'] });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(fetchMock).toHaveBeenLastCalledWith(
      '/preflop_walk',
      expect.objectContaining({ body: JSON.stringify({ stack_bb: 100, action_path: ['raise'], players: 2 }) }),
    );
  });

  it('does not re-fetch when actionPath is replaced with an equal-content array', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(walkPayload()) });
    vi.stubGlobal('fetch', fetchMock);

    const { result, rerender } = renderHook(({ actionPath }) => usePreflopWalk(100, actionPath), {
      initialProps: { actionPath: ['raise'] as string[] },
    });
    await waitFor(() => expect(result.current.loading).toBe(false));

    // A fresh array with the same contents — pathKey (a joined string)
    // must keep the effect from re-firing, proving the array itself
    // isn't smuggled into the dependency array.
    rerender({ actionPath: ['raise'] });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('aborts the in-flight request when actionPath changes before it resolves', async () => {
    const fetchMock = vi.fn().mockImplementation(
      () => new Promise(() => {}), // never resolves — simulates a slow in-flight request
    );
    vi.stubGlobal('fetch', fetchMock);

    const { rerender } = renderHook(({ actionPath }) => usePreflopWalk(100, actionPath), {
      initialProps: { actionPath: [] as string[] },
    });
    const firstSignal = fetchMock.mock.calls[0][1].signal as AbortSignal;
    expect(firstSignal.aborted).toBe(false);

    rerender({ actionPath: ['raise'] });
    expect(firstSignal.aborted).toBe(true);
  });
});
