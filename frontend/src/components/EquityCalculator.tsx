import { useState } from 'react';
import { fetchEquity, SolveError } from '../api';
import type { EquityResponse } from '../types';

/** M10: a standalone board-aware equity calculator, independent of the
 * range-grid solver above it — pick two concrete hands (and optionally
 * a board), see each side's win/tie share. See api/main.py's GET
 * /equity and poker_solver/board_equity.py for what's behind it. */
export function EquityCalculator() {
  const [handA, setHandA] = useState('AhAd');
  const [handB, setHandB] = useState('KhKd');
  const [board, setBoard] = useState('');
  const [result, setResult] = useState<EquityResponse | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  async function handleCalculate() {
    setError('');
    setLoading(true);
    try {
      const response = await fetchEquity(handA, handB, board);
      setResult(response);
    } catch (err) {
      setResult(null);
      setError(err instanceof SolveError ? err.message : 'Something went wrong');
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="equity-calculator">
      <h2>Equity calculator</h2>
      <div className="equity-inputs">
        <label>
          Hand A
          <input
            value={handA}
            onChange={(event) => setHandA(event.target.value)}
            placeholder="AhAd"
            aria-label="Hand A"
          />
        </label>
        <label>
          Hand B
          <input
            value={handB}
            onChange={(event) => setHandB(event.target.value)}
            placeholder="KhKd"
            aria-label="Hand B"
          />
        </label>
        <label>
          Board (optional)
          <input
            value={board}
            onChange={(event) => setBoard(event.target.value)}
            placeholder="2c7d9h"
            aria-label="Board"
          />
        </label>
        <button type="button" onClick={handleCalculate} disabled={loading}>
          {loading ? 'Calculating…' : 'Calculate'}
        </button>
      </div>

      {error && (
        <p className="equity-error" role="alert">
          {error}
        </p>
      )}

      {result && (
        <div className="equity-result">
          {[
            { label: result.hand_a, value: result.equity_a },
            { label: result.hand_b, value: result.equity_b },
          ].map(({ label, value }) => {
            const pct = (value * 100).toFixed(1);
            return (
              <div className="detail-row" key={label}>
                <span className="label">{label}</span>
                <span className="bar-track">
                  <span className="bar-fill" style={{ width: `${pct}%`, background: 'var(--call)' }} />
                </span>
                <span className="pct">{pct}%</span>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
