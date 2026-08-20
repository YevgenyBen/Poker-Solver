import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ActionPathSolver } from './ActionPathSolver';

function mockPathResponse(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    ok: true,
    json: () =>
      Promise.resolve({
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
          '8d4d': { call_or_check: 0.33, 'raise:7.50': 0.33, 'all_in:97.50': 0.34 },
        },
        ...overrides,
      }),
  };
}

describe('ActionPathSolver', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('renders inputs prefilled with defaults and a preset selector', () => {
    render(<ActionPathSolver />);
    expect(screen.getByLabelText('Preflop line')).toHaveValue('open_call');
    expect(screen.getByLabelText('Stack (bb)')).toHaveValue(100);
    expect(screen.getByLabelText('Board')).toHaveValue('Jh7d2c');
    expect(screen.getByRole('button', { name: 'Solve' })).toBeInTheDocument();
  });

  it('solves and shows each combo on click, POSTing the right body', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockPathResponse());
    vi.stubGlobal('fetch', fetchMock);

    render(<ActionPathSolver />);
    fireEvent.click(screen.getByRole('button', { name: 'Solve' }));

    await waitFor(() => expect(screen.getByText('AdAc')).toBeInTheDocument());
    expect(screen.getByText('8d4d')).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith('/solve_flop_from_path', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ stack_bb: 100, action_path: ['raise', 'call_or_check'], board: 'Jh7d2c' }),
      signal: undefined,
    });
  });

  it('sends the 3-bet preset action path when selected', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockPathResponse());
    vi.stubGlobal('fetch', fetchMock);

    render(<ActionPathSolver />);
    fireEvent.change(screen.getByLabelText('Preflop line'), { target: { value: 'open_3bet_call' } });
    fireEvent.click(screen.getByRole('button', { name: 'Solve' }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith('/solve_flop_from_path', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          stack_bb: 100,
          action_path: ['raise', 'raise', 'call_or_check'],
          board: 'Jh7d2c',
        }),
        signal: undefined,
      }),
    );
  });

  it('shows a "Solved live" indicator when hit is false', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockPathResponse({ hit: false })));

    render(<ActionPathSolver />);
    fireEvent.click(screen.getByRole('button', { name: 'Solve' }));

    await waitFor(() => expect(screen.getByText('Solved live')).toBeInTheDocument());
  });

  it('shows a "Cache hit" indicator when hit is true', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockPathResponse({ hit: true, elapsed_seconds: 0.0007 })));

    render(<ActionPathSolver />);
    fireEvent.click(screen.getByRole('button', { name: 'Solve' }));

    await waitFor(() => expect(screen.getByText('Cache hit')).toBeInTheDocument());
  });

  it('shows an error message and no result on a rejected request', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 422,
        json: () => Promise.resolve({ detail: "step 1: 'fold' is not legal at this node" }),
      }),
    );

    render(<ActionPathSolver />);
    fireEvent.click(screen.getByRole('button', { name: 'Solve' }));

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent("step 1: 'fold' is not legal at this node"),
    );
    expect(screen.queryByText('AdAc')).not.toBeInTheDocument();
  });

  it('clears a previous error once a new calculation succeeds', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: false,
        status: 422,
        json: () => Promise.resolve({ detail: 'bad input' }),
      })
      .mockResolvedValueOnce(mockPathResponse());
    vi.stubGlobal('fetch', fetchMock);

    render(<ActionPathSolver />);
    fireEvent.click(screen.getByRole('button', { name: 'Solve' }));
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Solve' }));
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
    expect(screen.getByText('AdAc')).toBeInTheDocument();
  });

  it('clears a stale result when the preset changes', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockPathResponse());
    vi.stubGlobal('fetch', fetchMock);

    render(<ActionPathSolver />);
    fireEvent.click(screen.getByRole('button', { name: 'Solve' }));
    await waitFor(() => expect(screen.getByText('AdAc')).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText('Preflop line'), { target: { value: 'limp_check' } });
    expect(screen.queryByText('AdAc')).not.toBeInTheDocument();
  });
});
