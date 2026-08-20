import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { useHashRoute } from './useHashRoute';

describe('useHashRoute', () => {
  afterEach(() => {
    window.location.hash = '';
  });

  it('defaults to the given route when there is no hash', () => {
    const { result } = renderHook(() => useHashRoute('preflop'));
    expect(result.current[0]).toBe('preflop');
  });

  it('reads an existing hash on mount', () => {
    window.location.hash = '#equity';
    const { result } = renderHook(() => useHashRoute('preflop'));
    expect(result.current[0]).toBe('equity');
  });

  it('setRoute updates both the returned route and the URL hash', () => {
    const { result } = renderHook(() => useHashRoute('preflop'));

    act(() => result.current[1]('flop'));

    expect(result.current[0]).toBe('flop');
    expect(window.location.hash).toBe('#flop');
  });

  it('responds to an externally-dispatched hashchange (e.g. browser back/forward)', () => {
    const { result } = renderHook(() => useHashRoute('preflop'));

    act(() => {
      window.location.hash = '#action-path';
      window.dispatchEvent(new Event('hashchange'));
    });

    expect(result.current[0]).toBe('action-path');
  });

  it('falls back to the default route if the hash is cleared', () => {
    window.location.hash = '#equity';
    const { result } = renderHook(() => useHashRoute('preflop'));
    expect(result.current[0]).toBe('equity');

    act(() => {
      window.location.hash = '';
      window.dispatchEvent(new Event('hashchange'));
    });

    expect(result.current[0]).toBe('preflop');
  });
});
