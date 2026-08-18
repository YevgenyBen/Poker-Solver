import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { App } from './App';

function mockResponseFor(stackBb: number) {
  return {
    ok: true,
    json: () =>
      Promise.resolve({
        stack_bb: stackBb,
        iterations: 1000,
        elapsed_seconds: 2.5,
        opening_range: Object.fromEntries(
          ['AA', 'KK', '72o'].map((hand) => [hand, { fold: hand === '72o' ? 0.8 : 0.0, raise: hand === '72o' ? 0.2 : 1.0 }]),
        ),
      }),
  };
}

describe('App', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('loads the default stack on mount and renders the grid', async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockResponseFor(100));
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);

    expect(fetchMock).toHaveBeenCalledWith('/solve/100', expect.anything());
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/solved in 2.50s/i));
    expect(screen.getAllByRole('button', { name: /^[AKQJT2-9]/ })).not.toHaveLength(0);
  });

  it('clicking a hand shows its breakdown in the detail panel', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(mockResponseFor(100)));
    const user = userEvent.setup();
    render(<App />);

    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/solved/i));
    await user.click(screen.getByText('72o'));

    expect(screen.getByRole('heading', { name: '72o' })).toBeInTheDocument();
    expect(screen.getByText('80.0%')).toBeInTheDocument();
  });

  it('switching stack depth via a preset re-solves', async () => {
    const fetchMock = vi.fn((url: string) => Promise.resolve(mockResponseFor(Number(url.split('/').pop()))));
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();
    render(<App />);

    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(/solved/i));
    await user.click(screen.getByRole('button', { name: '50bb' }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/solve/50', expect.anything()));
  });
});
