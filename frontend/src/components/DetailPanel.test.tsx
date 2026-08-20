import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { DetailPanel } from './DetailPanel';

describe('DetailPanel', () => {
  it('shows a placeholder when no hand is selected', () => {
    render(<DetailPanel hand={null} freqs={null} trained={null} />);
    expect(screen.getByText(/click a hand/i)).toBeInTheDocument();
  });

  it('shows the hand label and each action percentage when selected', () => {
    render(<DetailPanel hand="AKs" freqs={{ fold: 0.1, raise: 0.9 }} trained={true} />);
    expect(screen.getByRole('heading', { name: 'AKs' })).toBeInTheDocument();
    expect(screen.getByText('fold')).toBeInTheDocument();
    expect(screen.getByText('10.0%')).toBeInTheDocument();
    expect(screen.getByText('raise')).toBeInTheDocument();
    expect(screen.getByText('90.0%')).toBeInTheDocument();
  });

  it('shows no untrained warning when trained is true', () => {
    render(<DetailPanel hand="AA" freqs={{ fold: 0.01, raise: 0.99 }} trained={true} />);
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('shows no untrained warning when trained is null (not wired through)', () => {
    render(<DetailPanel hand="AA" freqs={{ fold: 0.01, raise: 0.99 }} trained={null} />);
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('shows an untrained warning when trained is explicitly false', () => {
    render(<DetailPanel hand="72o" freqs={{ fold: 0.5, raise: 0.5 }} trained={false} />);
    expect(screen.getByRole('status')).toHaveTextContent(/not enough data/i);
  });
});
