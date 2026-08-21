import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ComboRow } from './ComboRow';

const FREQS = { fold: 0.1, call_or_check: 0.3, 'raise:2.50': 0.6 };

describe('ComboRow', () => {
  it('renders the label and a breakdown of every visible action', () => {
    render(<ComboRow label="AsKs" freqs={FREQS} />);
    expect(screen.getByText('AsKs')).toBeInTheDocument();
    const row = screen.getByText('AsKs').closest('.detail-row')!;
    expect(row).toHaveTextContent('fold 10%');
    expect(row).toHaveTextContent('call_or_check 30%');
    expect(row).toHaveTextContent('raise:2.50 60%');
  });

  it('omits the label entirely when the caller titles the row itself', () => {
    const { container } = render(<ComboRow freqs={FREQS} />);
    expect(container.querySelector('.label')).toBeNull();
    expect(container.querySelector('.bar-fill')).not.toBeNull();
  });

  it('hides frequencies below the visible threshold rather than showing 0%', () => {
    render(<ComboRow label="72o" freqs={{ fold: 0.999, 'raise:2.50': 0.001 }} />);
    const row = screen.getByText('72o').closest('.detail-row')!;
    expect(row).toHaveTextContent('fold 100%');
    expect(row).not.toHaveTextContent('raise:2.50');
  });

  it('shows no confidence indicator by default — absent means trusted', () => {
    render(<ComboRow label="AsKs" freqs={FREQS} />);
    expect(screen.queryByText('low data')).not.toBeInTheDocument();
  });

  it('shows the low-data indicator only when explicitly untrained', () => {
    render(<ComboRow label="AsKs" freqs={FREQS} trained={false} />);
    expect(screen.getByText('low data')).toBeInTheDocument();
  });
});
