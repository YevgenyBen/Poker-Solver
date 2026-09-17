import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { PRIORITY_NOTES } from '../advisoryNotes';
import { DecisionWarnings } from './DecisionWarnings';

const notes = [
  { id: 'street-unmeasured', text: 'Accuracy here is unmeasured.' },
  { id: 'standing-aggression-caveat', text: 'How often to bet is a rough hint.' },
  { id: 'facing-a-bet-cost', text: 'Facing a bet is where the cost lives.' },
  { id: 'turn-shove', text: 'Shoving here cost at least 2.4 big blinds.' },
  { id: 'multiway-bet', text: 'This engine bets three times as often.' },
];

describe('DecisionWarnings', () => {
  it('shows each priority warning on its own, with the backend text', () => {
    render(<DecisionWarnings notes={notes} />);
    const alerts = screen.getAllByRole('alert');
    expect(alerts).toHaveLength(2);
    expect(alerts[0]).toHaveTextContent(PRIORITY_NOTES['multiway-bet']);
    expect(alerts[0]).toHaveTextContent('This engine bets three times as often.');
    expect(alerts[1]).toHaveTextContent('Shoving here cost at least 2.4 big blinds.');
  });

  it('collapses the rest behind a count', () => {
    render(<DecisionWarnings notes={notes} />);
    const summary = screen.getByText('Other caveats for this decision (3)');
    const details = summary.closest('details') as HTMLElement;
    expect(details).not.toHaveAttribute('open');
    expect(within(details).getByText('Facing a bet is where the cost lives.')).toBeInTheDocument();
  });

  it('renders nothing without notes, and no drawer without other notes', () => {
    const { container } = render(<DecisionWarnings notes={[]} />);
    expect(container).toBeEmptyDOMElement();
    render(<DecisionWarnings notes={[notes[3]]} />);
    expect(screen.queryByText(/Other caveats/)).not.toBeInTheDocument();
  });
});
