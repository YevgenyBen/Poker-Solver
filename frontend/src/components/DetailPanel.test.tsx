import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { DetailPanel } from './DetailPanel';

describe('DetailPanel', () => {
  it('shows a placeholder when no hand is selected', () => {
    render(<DetailPanel hand={null} freqs={null} />);
    expect(screen.getByText(/click a hand/i)).toBeInTheDocument();
  });

  it('shows the hand label and each action percentage when selected', () => {
    render(<DetailPanel hand="AKs" freqs={{ fold: 0.1, raise: 0.9 }} />);
    expect(screen.getByRole('heading', { name: 'AKs' })).toBeInTheDocument();
    expect(screen.getByText('fold')).toBeInTheDocument();
    expect(screen.getByText('10.0%')).toBeInTheDocument();
    expect(screen.getByText('raise')).toBeInTheDocument();
    expect(screen.getByText('90.0%')).toBeInTheDocument();
  });
});
