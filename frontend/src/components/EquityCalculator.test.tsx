import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { EquityCalculator } from './EquityCalculator';

function mockEquityResponse(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    ok: true,
    json: () =>
      Promise.resolve({
        hand_a: 'AhAd',
        hand_b: 'KhKd',
        board: '',
        equity_a: 0.82,
        equity_b: 0.18,
        ...overrides,
      }),
  };
}

describe('EquityCalculator', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('renders inputs prefilled with a default matchup', () => {
    render(<EquityCalculator />);
    expect(screen.getByLabelText('Hand A')).toHaveValue('AhAd');
    expect(screen.getByLabelText('Hand B')).toHaveValue('KhKd');
    expect(screen.getByLabelText('Board')).toHaveValue('');
  });

  it('calculates and shows each hand equity on click', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockEquityResponse());
    vi.stubGlobal('fetch', fetchMock);

    render(<EquityCalculator />);
    fireEvent.click(screen.getByRole('button', { name: /calculate/i }));

    await waitFor(() => expect(screen.getByText('82.0%')).toBeInTheDocument());
    expect(screen.getByText('18.0%')).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith('/equity?hand_a=AhAd&hand_b=KhKd&board=', { signal: undefined });
  });

  it('sends whatever the user typed, including a board', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(mockEquityResponse({ hand_a: '4h3h', hand_b: 'QsQd', board: '2c7d9h' }));
    vi.stubGlobal('fetch', fetchMock);

    render(<EquityCalculator />);
    fireEvent.change(screen.getByLabelText('Hand A'), { target: { value: '3h4h' } });
    fireEvent.change(screen.getByLabelText('Hand B'), { target: { value: 'QsQd' } });
    fireEvent.change(screen.getByLabelText('Board'), { target: { value: '2c7d9h' } });
    fireEvent.click(screen.getByRole('button', { name: /calculate/i }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith('/equity?hand_a=3h4h&hand_b=QsQd&board=2c7d9h', {
        signal: undefined,
      }),
    );
  });

  it('shows an error message and no result on a rejected request', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 422,
        json: () => Promise.resolve({ detail: 'hand_a and hand_b share a card' }),
      }),
    );

    render(<EquityCalculator />);
    fireEvent.click(screen.getByRole('button', { name: /calculate/i }));

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('hand_a and hand_b share a card'));
    expect(screen.queryByText(/%/)).not.toBeInTheDocument();
  });

  it('clears a previous error once a new calculation succeeds', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: false,
        status: 422,
        json: () => Promise.resolve({ detail: 'bad input' }),
      })
      .mockResolvedValueOnce(mockEquityResponse());
    vi.stubGlobal('fetch', fetchMock);

    render(<EquityCalculator />);
    fireEvent.click(screen.getByRole('button', { name: /calculate/i }));
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /calculate/i }));
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
    expect(screen.getByText('82.0%')).toBeInTheDocument();
  });
});
