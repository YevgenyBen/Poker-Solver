import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { TableModeControl } from './TableModeControl';

describe('TableModeControl', () => {
  it('renders the players toggle and marks the active mode', () => {
    render(
      <TableModeControl players={2} position="BTN" onPlayersChange={() => {}} onPositionChange={() => {}} />,
    );
    expect(screen.getByRole('button', { name: 'Heads-up' })).toHaveClass('active');
    expect(screen.getByRole('button', { name: '3-max (demo)' })).not.toHaveClass('active');
    expect(screen.getByRole('button', { name: '6-max (demo)' })).not.toHaveClass('active');
    expect(screen.getByRole('button', { name: '9-max (demo)' })).not.toHaveClass('active');
  });

  it('does not render a position selector in heads-up mode', () => {
    render(
      <TableModeControl players={2} position="BTN" onPlayersChange={() => {}} onPositionChange={() => {}} />,
    );
    expect(screen.queryByRole('group', { name: 'Position' })).not.toBeInTheDocument();
  });

  it('renders a button for every 3-max position and marks the active one', () => {
    render(
      <TableModeControl players={3} position="SB" onPlayersChange={() => {}} onPositionChange={() => {}} />,
    );
    for (const pos of ['BTN', 'SB', 'BB']) {
      expect(screen.getByRole('button', { name: pos })).toBeInTheDocument();
    }
    expect(screen.getByRole('button', { name: 'SB' })).toHaveClass('active');
    expect(screen.getByRole('button', { name: 'BTN' })).not.toHaveClass('active');
  });

  it('renders a button for every 6-max position', () => {
    render(
      <TableModeControl players={6} position="CO" onPlayersChange={() => {}} onPositionChange={() => {}} />,
    );
    for (const pos of ['UTG', 'MP', 'CO', 'BTN', 'SB', 'BB']) {
      expect(screen.getByRole('button', { name: pos })).toBeInTheDocument();
    }
    expect(screen.getByRole('button', { name: 'CO' })).toHaveClass('active');
  });

  it('renders a button for every 9-max position', () => {
    render(
      <TableModeControl players={9} position="MP2" onPlayersChange={() => {}} onPositionChange={() => {}} />,
    );
    for (const pos of ['UTG', 'UTG1', 'MP1', 'MP2', 'MP3', 'CO', 'BTN', 'SB', 'BB']) {
      expect(screen.getByRole('button', { name: pos })).toBeInTheDocument();
    }
    expect(screen.getByRole('button', { name: 'MP2' })).toHaveClass('active');
  });

  it.each([
    ['3-max (demo)', 3],
    ['6-max (demo)', 6],
    ['9-max (demo)', 9],
  ] as const)('clicking %s calls onPlayersChange(%d)', (label, expected) => {
    const onPlayersChange = vi.fn();
    render(
      <TableModeControl
        players={2}
        position="BTN"
        onPlayersChange={onPlayersChange}
        onPositionChange={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: label }));
    expect(onPlayersChange).toHaveBeenCalledWith(expected);
  });

  it('clicking a position button calls onPositionChange with that position', () => {
    const onPositionChange = vi.fn();
    render(
      <TableModeControl
        players={3}
        position="BTN"
        onPlayersChange={() => {}}
        onPositionChange={onPositionChange}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'BB' }));
    expect(onPositionChange).toHaveBeenCalledWith('BB');
  });
});
