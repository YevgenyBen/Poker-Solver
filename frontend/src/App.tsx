import { ActionPathSolver } from './components/ActionPathSolver';
import { AdviseSolver } from './components/AdviseSolver';
import { CachedFlopSolver } from './components/CachedFlopSolver';
import { EquityCalculator } from './components/EquityCalculator';
import { FlopSolver } from './components/FlopSolver';
import { MultiwayFlopSolver } from './components/MultiwayFlopSolver';
import { PreflopRangesPage } from './components/PreflopRangesPage';
import { TabNav } from './components/TabNav';
import { TurnPathSolver } from './components/TurnPathSolver';
import { useHashRoute } from './useHashRoute';

const TABS = [
  { id: 'advise', label: 'Advisor' },
  { id: 'preflop', label: 'Preflop Ranges' },
  { id: 'equity', label: 'Equity Calculator' },
  { id: 'flop', label: 'Flop Solver' },
  { id: 'flop-cached', label: 'Cached Flop Solver' },
  { id: 'flop-multiway', label: 'Multiway Flop Solver' },
  { id: 'action-path', label: 'Action-Path Wizard' },
  { id: 'turn', label: 'Turn Advisor' },
] as const;

const DEFAULT_TAB = 'preflop';
const TAB_IDS = new Set<string>(TABS.map((tab) => tab.id));

// The app shell: an <h1> app title, a tab bar, and exactly one active
// tab's page mounted at a time (unmounting the rest, not just hiding
// them — see useHashRoute.ts and each page's own component for why:
// RangeGrid/ActionPathSolver both fetch eagerly on mount, so keeping
// every tab mounted simultaneously would keep re-triggering all of
// them regardless of which the user is actually looking at).
export function App() {
  const [hashRoute, setHashRoute] = useHashRoute(DEFAULT_TAB);
  const activeTab = TAB_IDS.has(hashRoute) ? hashRoute : DEFAULT_TAB;

  return (
    <>
      <header className="app-header">
        <h1>Poker Solver</h1>
      </header>

      <TabNav tabs={[...TABS]} activeTab={activeTab} onSelect={setHashRoute} />

      <main className="tab-page" id="tab-panel" role="tabpanel" aria-labelledby={`tab-${activeTab}`}>
        {activeTab === 'advise' && <AdviseSolver />}
        {activeTab === 'preflop' && <PreflopRangesPage />}
        {activeTab === 'equity' && <EquityCalculator />}
        {activeTab === 'flop' && <FlopSolver />}
        {activeTab === 'flop-cached' && <CachedFlopSolver />}
        {activeTab === 'flop-multiway' && <MultiwayFlopSolver />}
        {activeTab === 'action-path' && <ActionPathSolver />}
        {activeTab === 'turn' && <TurnPathSolver />}
      </main>
    </>
  );
}
