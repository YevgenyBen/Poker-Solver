import { PRIORITY_NOTES, splitNotes } from '../advisoryNotes';
import type { AdvisoryNote } from '../types';

/** A2: priority warnings on their own, the rest collapsed. */
export function DecisionWarnings({ notes }: { notes: AdvisoryNote[] }) {
  if (notes.length === 0) return null;
  const { priority, other } = splitNotes(notes);
  return (
    <div className="decision-warnings">
      {priority.map((note) => (
        <div key={note.id} className="solver-warning priority-warning" role="alert" data-note={note.id}>
          <strong>{PRIORITY_NOTES[note.id]}</strong>
          <p>{note.text}</p>
        </div>
      ))}
      {other.length > 0 && (
        <details className="other-caveats">
          <summary>Other caveats for this decision ({other.length})</summary>
          <ul>
            {other.map((note) => (
              <li key={note.id} data-note={note.id}>
                {note.text}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
