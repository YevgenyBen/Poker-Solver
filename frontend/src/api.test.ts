import { afterEach, describe, expect, it, vi } from 'vitest';
import { fetchOpeningRange, SolveError } from './api';

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
});
