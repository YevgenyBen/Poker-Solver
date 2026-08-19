import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { FlopSolver } from './FlopSolver';

function mockFlopResponse(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    ok: true,
    json: () =>
      Promise.resolve({
        board: 'Jh7d2c',
        pot: 10,
        stack_bb: 40,
        iterations: 1000,
        elapsed_seconds: 2.5,
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

describe('FlopSolver', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('renders inputs prefilled with a default board/pot/stack', () => {
    render(<FlopSolver />);
    expect(screen.getByLabelText('Board')).toHaveValue('Jh7d2c');
    expect(screen.getByLabelText('Pot')).toHaveValue(10);
    expect(screen.getByLabelText('Stack (bb)')).toHaveValue(40);
    expect(screen.getByLabelText('Position')).toHaveValue('OOP');
  });

  it('solves and shows each combo on click', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockFlopResponse());
    vi.stubGlobal('fetch', fetchMock);

    render(<FlopSolver />);
    fireEvent.click(screen.getByRole('button', { name: /solve flop/i }));

    await waitFor(() => expect(screen.getByText('AdAc')).toBeInTheDocument());
    expect(screen.getByText('8d4d')).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      '/solve_flop?board=Jh7d2c&pot=10&stack_bb=40&position=OOP',
      { signal: undefined },
    );
  });

  it('sends whatever the user typed, including a chosen position', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockFlopResponse({ position: 'IP' }));
    vi.stubGlobal('fetch', fetchMock);

    render(<FlopSolver />);
    fireEvent.change(screen.getByLabelText('Board'), { target: { value: '9h8h7h' } });
    fireEvent.change(screen.getByLabelText('Pot'), { target: { value: '20' } });
    fireEvent.change(screen.getByLabelText('Stack (bb)'), { target: { value: '60' } });
    fireEvent.change(screen.getByLabelText('Position'), { target: { value: 'IP' } });
    fireEvent.click(screen.getByRole('button', { name: /solve flop/i }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/solve_flop?board=9h8h7h&pot=20&stack_bb=60&position=IP',
        { signal: undefined },
      ),
    );
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

    render(<FlopSolver />);
    fireEvent.click(screen.getByRole('button', { name: /solve flop/i }));

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
      .mockResolvedValueOnce(mockFlopResponse());
    vi.stubGlobal('fetch', fetchMock);

    render(<FlopSolver />);
    fireEvent.click(screen.getByRole('button', { name: /solve flop/i }));
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /solve flop/i }));
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
    expect(screen.getByText('AdAc')).toBeInTheDocument();
  });
});
