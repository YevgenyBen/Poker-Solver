import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { CachedFlopSolver } from './CachedFlopSolver';

function mockQueryResponse(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    ok: true,
    json: () =>
      Promise.resolve({
        board: 'Jh7d2c',
        canonical_board: 'Jc2d7h',
        pot: 10,
        stack_bb: 40,
        canonical_stack_bb: 40,
        hit: false,
        elapsed_seconds: 0.95,
        position: 'OOP',
        positions: ['OOP', 'IP'],
        strategy: {
          AdAc: { call_or_check: 0.02, 'raise:7.50': 0.6, 'all_in:40.00': 0.38 },
          '8d4d': { call_or_check: 0.33, 'raise:7.50': 0.33, 'all_in:40.00': 0.34 },
        },
        ...overrides,
      }),
  };
}

describe('CachedFlopSolver', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('renders inputs prefilled with a default board/stack and a fixed pot', () => {
    render(<CachedFlopSolver />);
    expect(screen.getByLabelText('Board')).toHaveValue('Jh7d2c');
    expect(screen.getByLabelText('Stack (bb)')).toHaveValue(40);
    expect(screen.queryByLabelText('Pot')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Position')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Solve' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Shuffle suits' })).toBeInTheDocument();
  });

  it('solves and shows each combo on click, calling the right URL', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockQueryResponse());
    vi.stubGlobal('fetch', fetchMock);

    render(<CachedFlopSolver />);
    fireEvent.click(screen.getByRole('button', { name: 'Solve' }));

    await waitFor(() => expect(screen.getByText('AdAc')).toBeInTheDocument());
    expect(screen.getByText('8d4d')).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith('/solve_flop_cached?board=Jh7d2c&stack_bb=40', { signal: undefined });
  });

  it('shows a "Solved live" indicator when hit is false', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockQueryResponse({ hit: false })));

    render(<CachedFlopSolver />);
    fireEvent.click(screen.getByRole('button', { name: 'Solve' }));

    await waitFor(() => expect(screen.getByText('Solved live')).toBeInTheDocument());
  });

  it('shows a "Cache hit" indicator when hit is true', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockQueryResponse({ hit: true, elapsed_seconds: 0.00015 })));

    render(<CachedFlopSolver />);
    fireEvent.click(screen.getByRole('button', { name: 'Solve' }));

    await waitFor(() => expect(screen.getByText('Cache hit')).toBeInTheDocument());
  });

  it('displays the canonical board and stack distinctly from the raw query values', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(mockQueryResponse({ canonical_board: 'Jc2d7h', canonical_stack_bb: 40 })),
    );

    render(<CachedFlopSolver />);
    fireEvent.click(screen.getByRole('button', { name: 'Solve' }));

    await waitFor(() => expect(screen.getByText(/Jc2d7h/)).toBeInTheDocument());
  });

  it('shows an error message and no result on a rejected request', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 422,
        json: () => Promise.resolve({ detail: 'board must have exactly 3 cards for a flop, got 2' }),
      }),
    );

    render(<CachedFlopSolver />);
    fireEvent.click(screen.getByRole('button', { name: 'Solve' }));

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent('board must have exactly 3 cards for a flop, got 2'),
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
      .mockResolvedValueOnce(mockQueryResponse());
    vi.stubGlobal('fetch', fetchMock);

    render(<CachedFlopSolver />);
    fireEvent.click(screen.getByRole('button', { name: 'Solve' }));
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Solve' }));
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
    expect(screen.getByText('AdAc')).toBeInTheDocument();
  });

  it('shuffle suits rewrites the board input by rotating each card\'s suit', () => {
    render(<CachedFlopSolver />);
    fireEvent.click(screen.getByRole('button', { name: 'Shuffle suits' }));
    // c->d->h->s->c: Jh7d2c -> Js7h2d
    expect(screen.getByLabelText('Board')).toHaveValue('Js7h2d');
  });

  it('shuffle suits is a safe no-op on malformed board text', () => {
    render(<CachedFlopSolver />);
    fireEvent.change(screen.getByLabelText('Board'), { target: { value: 'Jh7d' } });
    fireEvent.click(screen.getByRole('button', { name: 'Shuffle suits' }));
    expect(screen.getByLabelText('Board')).toHaveValue('Jh7d');
  });
});
