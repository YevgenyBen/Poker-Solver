import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { StackControl } from './StackControl';

describe('StackControl', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('renders the initial stack value and all preset buttons', () => {
    render(<StackControl initialStackBb={100} onChange={() => {}} />);
    expect(screen.getByText('100bb', { selector: '.stack-value' })).toBeInTheDocument();
    for (const depth of [20, 40, 50, 75, 100, 150, 200]) {
      expect(screen.getByRole('button', { name: `${depth}bb` })).toBeInTheDocument();
    }
  });

  it('marks the preset matching the initial value as active', () => {
    render(<StackControl initialStackBb={50} onChange={() => {}} />);
    expect(screen.getByRole('button', { name: '50bb' })).toHaveClass('active');
    expect(screen.getByRole('button', { name: '100bb' })).not.toHaveClass('active');
  });

  it('clicking a preset calls onChange immediately, no debounce needed', () => {
    const onChange = vi.fn();
    render(<StackControl initialStackBb={100} onChange={onChange} />);

    fireEvent.click(screen.getByRole('button', { name: '40bb' }));
    expect(onChange).toHaveBeenCalledWith(40);
    expect(screen.getByText('40bb', { selector: '.stack-value' })).toBeInTheDocument();
  });

  it('debounces slider input before calling onChange', () => {
    const onChange = vi.fn();
    render(<StackControl initialStackBb={100} onChange={onChange} />);
    const slider = screen.getByRole('slider');

    fireEvent.change(slider, { target: { value: '150' } });
    expect(onChange).not.toHaveBeenCalled();

    vi.advanceTimersByTime(349);
    expect(onChange).not.toHaveBeenCalled();

    vi.advanceTimersByTime(1);
    expect(onChange).toHaveBeenCalledWith(150);
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it('only fires once for rapid successive slider changes', () => {
    const onChange = vi.fn();
    render(<StackControl initialStackBb={100} onChange={onChange} />);
    const slider = screen.getByRole('slider');

    fireEvent.change(slider, { target: { value: '120' } });
    vi.advanceTimersByTime(100);
    fireEvent.change(slider, { target: { value: '140' } });
    vi.advanceTimersByTime(349);
    expect(onChange).not.toHaveBeenCalled();

    vi.advanceTimersByTime(1);
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith(140);
  });
});
