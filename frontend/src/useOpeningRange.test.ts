import { renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useOpeningRange } from './useOpeningRange';

describe('useOpeningRange', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('fetches on mount and reports the solved status', async () => {
    const payload = { stack_bb: 100, iterations: 1000, elapsed_seconds: 1.23, opening_range: { AA: { fold: 0 } } };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: () => Promise.resolve(payload) }));

    const { result } = renderHook(() => useOpeningRange(100));

    expect(result.current.loading).toBe(true);
    expect(result.current.status).toBe('Solving…');

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toEqual(payload);
    expect(result.current.status).toBe('Solved in 1.23s (1000 iterations)');
  });

  it('surfaces a server error message in status without touching data', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 422,
        json: () => Promise.resolve({ detail: 'stack_bb must be positive' }),
      }),
    );

    const { result } = renderHook(() => useOpeningRange(-5));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.status).toBe('Error: stack_bb must be positive');
    expect(result.current.data).toBeNull();
  });

  it('re-fetches when stackBb changes', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ stack_bb: 100, iterations: 1, elapsed_seconds: 0, opening_range: {} }),
    });
    vi.stubGlobal('fetch', fetchMock);

    const { result, rerender } = renderHook(({ stackBb }) => useOpeningRange(stackBb), {
      initialProps: { stackBb: 100 },
    });
    await waitFor(() => expect(result.current.loading).toBe(false));

    rerender({ stackBb: 50 });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(fetchMock).toHaveBeenLastCalledWith('/solve/50', expect.anything());
  });
});
