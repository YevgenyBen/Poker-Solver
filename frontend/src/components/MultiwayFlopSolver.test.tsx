import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MultiwayFlopSolver } from './MultiwayFlopSolver';

function mockMultiwayFlopResponse(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    ok: true,
    json: () =>
      Promise.resolve({
        board: 'Jh7d2c',
        pot: 10,
        stack_bb: 40,
        iterations: 200,
        elapsed_seconds: 3.1,
        position: 'OOP',
        positions: ['OOP', 'MID', 'IP'],
        strategy: {
          AsKs: { call_or_check: 0.02, 'raise:25.00': 0.6, 'all_in:40.00': 0.38 },
          QcJc: { call_or_check: 0.33, 'raise:25.00': 0.33, 'all_in:40.00': 0.34 },
        },
        trained: { AsKs: true, QcJc: true },
        ...overrides,
      }),
  };
}

describe('MultiwayFlopSolver', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('renders inputs prefilled with a default board/pot/stack/depth, and 3 positions', () => {
    render(<MultiwayFlopSolver />);
    expect(screen.getByLabelText('Board')).toHaveValue('Jh7d2c');
    expect(screen.getByLabelText('Pot')).toHaveValue(10);
    expect(screen.getByLabelText('Stack (bb)')).toHaveValue(40);
    expect(screen.getByLabelText('Position')).toHaveValue('OOP');
    expect(screen.getByLabelText('Runout depth')).toHaveValue('flop');
    expect(screen.getByRole('button', { name: 'Solve flop' })).toBeInTheDocument();

    const positionOptions = screen.getAllByRole<HTMLOptionElement>('option', { name: /^(OOP|MID|IP)$/ });
    expect(positionOptions.map((option) => option.value)).toEqual(['OOP', 'MID', 'IP']);
  });

  it('offers a flop-to-river depth option (M40)', () => {
    render(<MultiwayFlopSolver />);
    expect(screen.getByRole('option', { name: 'Flop + turn + river' })).toBeInTheDocument();
  });

  it('solves and shows each combo on click, calling /solve_flop_multiway', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockMultiwayFlopResponse());
    vi.stubGlobal('fetch', fetchMock);

    render(<MultiwayFlopSolver />);
    fireEvent.click(screen.getByRole('button', { name: /solve flop/i }));

    await waitFor(() => expect(screen.getByText('AsKs')).toBeInTheDocument());
    expect(screen.getByText('QcJc')).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      '/solve_flop_multiway?board=Jh7d2c&pot=10&stack_bb=40&position=OOP',
      { signal: undefined },
    );
  });

  it('sends a chosen MID position', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockMultiwayFlopResponse({ position: 'MID' }));
    vi.stubGlobal('fetch', fetchMock);

    render(<MultiwayFlopSolver />);
    fireEvent.change(screen.getByLabelText('Position'), { target: { value: 'MID' } });
    fireEvent.click(screen.getByRole('button', { name: /solve flop/i }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        '/solve_flop_multiway?board=Jh7d2c&pot=10&stack_bb=40&position=MID',
        { signal: undefined },
      ),
    );
  });

  it('solves flop+turn and calls /solve_flop_turn_multiway with the right query string', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockMultiwayFlopResponse());
    vi.stubGlobal('fetch', fetchMock);

    render(<MultiwayFlopSolver />);
    fireEvent.change(screen.getByLabelText('Runout depth'), { target: { value: 'flop_turn' } });
    fireEvent.click(screen.getByRole('button', { name: 'Solve flop + turn' }));

    await waitFor(() => expect(screen.getByText('AsKs')).toBeInTheDocument());
    expect(fetchMock).toHaveBeenCalledWith(
      '/solve_flop_turn_multiway?board=Jh7d2c&pot=10&stack_bb=40&position=OOP',
      { signal: undefined },
    );
  });

  it('solves flop+turn+river and calls /solve_flop_to_river_multiway with the right query string', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockMultiwayFlopResponse());
    vi.stubGlobal('fetch', fetchMock);

    render(<MultiwayFlopSolver />);
    fireEvent.change(screen.getByLabelText('Runout depth'), { target: { value: 'flop_to_river' } });
    fireEvent.click(screen.getByRole('button', { name: 'Solve flop + turn + river' }));

    await waitFor(() => expect(screen.getByText('AsKs')).toBeInTheDocument());
    expect(fetchMock).toHaveBeenCalledWith(
      '/solve_flop_to_river_multiway?board=Jh7d2c&pot=10&stack_bb=40&position=OOP',
      { signal: undefined },
    );
  });

  it('shows depth-appropriate button and loading copy', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockMultiwayFlopResponse());
    vi.stubGlobal('fetch', fetchMock);

    render(<MultiwayFlopSolver />);
    fireEvent.change(screen.getByLabelText('Runout depth'), { target: { value: 'flop_turn' } });
    expect(screen.getByRole('button', { name: 'Solve flop + turn' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Solve flop + turn' }));
    expect(screen.getByRole('button', { name: /solving flop \+ turn/i })).toBeInTheDocument();

    await waitFor(() => expect(screen.getByText('AsKs')).toBeInTheDocument());
  });

  it('clears a stale result when the depth changes', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockMultiwayFlopResponse());
    vi.stubGlobal('fetch', fetchMock);

    render(<MultiwayFlopSolver />);
    fireEvent.click(screen.getByRole('button', { name: 'Solve flop' }));
    await waitFor(() => expect(screen.getByText('AsKs')).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText('Runout depth'), { target: { value: 'flop_turn' } });
    expect(screen.queryByText('AsKs')).not.toBeInTheDocument();
  });

  it('marks an untrained combo with a low-data indicator, and not a trained one', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(mockMultiwayFlopResponse({ trained: { AsKs: true, QcJc: false } })),
    );

    render(<MultiwayFlopSolver />);
    fireEvent.click(screen.getByRole('button', { name: /solve flop/i }));

    await waitFor(() => expect(screen.getByText('AsKs')).toBeInTheDocument());
    expect(screen.getByText('low data')).toBeInTheDocument();
    const asKsRow = screen.getByText('AsKs').closest('.detail-row');
    expect(asKsRow).not.toHaveTextContent('low data');
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

    render(<MultiwayFlopSolver />);
    fireEvent.click(screen.getByRole('button', { name: /solve flop/i }));

    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent('board must have exactly 3 cards for a flop, got 2'),
    );
    expect(screen.queryByText('AsKs')).not.toBeInTheDocument();
  });

  it('reports all 3 positions in the result status line', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockMultiwayFlopResponse()));

    render(<MultiwayFlopSolver />);
    fireEvent.click(screen.getByRole('button', { name: /solve flop/i }));

    await waitFor(() => expect(screen.getByText(/OOP\/MID\/IP/)).toBeInTheDocument());
  });
});
