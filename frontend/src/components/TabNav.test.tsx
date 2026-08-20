import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { TabNav } from './TabNav';

const TABS = [
  { id: 'a', label: 'Tab A' },
  { id: 'b', label: 'Tab B' },
];

describe('TabNav', () => {
  it('renders one tab button per entry, marking the active one', () => {
    render(<TabNav tabs={TABS} activeTab="a" onSelect={() => {}} />);

    expect(screen.getByRole('tab', { name: 'Tab A' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: 'Tab B' })).toHaveAttribute('aria-selected', 'false');
  });

  it('calls onSelect with the clicked tab id', () => {
    const onSelect = vi.fn();
    render(<TabNav tabs={TABS} activeTab="a" onSelect={onSelect} />);

    fireEvent.click(screen.getByRole('tab', { name: 'Tab B' }));

    expect(onSelect).toHaveBeenCalledWith('b');
  });

  it('wires each tab to the shared panel via aria-controls', () => {
    render(<TabNav tabs={TABS} activeTab="a" onSelect={() => {}} />);

    expect(screen.getByRole('tab', { name: 'Tab A' })).toHaveAttribute('aria-controls', 'tab-panel');
    expect(screen.getByRole('tab', { name: 'Tab B' })).toHaveAttribute('aria-controls', 'tab-panel');
  });
});
