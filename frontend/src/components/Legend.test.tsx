import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Legend } from './Legend';

describe('Legend', () => {
  it('shows all four action labels', () => {
    render(<Legend />);
    expect(screen.getByText('fold')).toBeInTheDocument();
    expect(screen.getByText('call / check')).toBeInTheDocument();
    expect(screen.getByText('raise')).toBeInTheDocument();
    expect(screen.getByText('all-in')).toBeInTheDocument();
  });
});
