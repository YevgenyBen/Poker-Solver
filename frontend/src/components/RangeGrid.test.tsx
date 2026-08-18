import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { RangeGrid } from './RangeGrid';

describe('RangeGrid', () => {
  it('renders all 169 hands', () => {
    render(<RangeGrid openingRange={null} selectedHand={null} onSelect={() => {}} />);
    expect(screen.getAllByRole('button')).toHaveLength(169);
    expect(screen.getByText('AA')).toBeInTheDocument();
    expect(screen.getByText('72o')).toBeInTheDocument();
  });

  it('calls onSelect with the clicked hand', async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(<RangeGrid openingRange={null} selectedHand={null} onSelect={onSelect} />);

    await user.click(screen.getByText('AA'));
    expect(onSelect).toHaveBeenCalledWith('AA');
  });

  it('supports keyboard selection via Enter', async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(<RangeGrid openingRange={null} selectedHand={null} onSelect={onSelect} />);

    screen.getByText('KK').focus();
    await user.keyboard('{Enter}');
    expect(onSelect).toHaveBeenCalledWith('KK');
  });

  it('marks the selected hand', () => {
    render(<RangeGrid openingRange={null} selectedHand="QQ" onSelect={() => {}} />);
    expect(screen.getByText('QQ')).toHaveClass('selected');
    expect(screen.getByText('AA')).not.toHaveClass('selected');
  });
});
