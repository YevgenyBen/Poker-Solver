interface TabConfig {
  id: string;
  label: string;
}

interface TabNavProps {
  tabs: TabConfig[];
  activeTab: string;
  onSelect: (id: string) => void;
}

// A real ARIA tabs widget (role="tablist"/"tab"), not the simpler
// role="group" toggle-idiom TableModeControl uses elsewhere — this
// component's whole purpose is literal page navigation, so the full
// role/state/relationship wiring (each button's aria-controls pointing
// at the one panel, the panel's own aria-labelledby pointing back) is
// worth it even without roving-tabindex keyboard support (a "should,"
// not a "must," in the ARIA Authoring Practices — the relationship
// wiring is what matters for correct screen-reader announcement).
export function TabNav({ tabs, activeTab, onSelect }: TabNavProps) {
  return (
    <nav className="tab-nav" role="tablist" aria-label="Solver sections">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          type="button"
          role="tab"
          id={`tab-${tab.id}`}
          aria-selected={tab.id === activeTab}
          aria-controls="tab-panel"
          className={tab.id === activeTab ? 'active' : ''}
          onClick={() => onSelect(tab.id)}
        >
          {tab.label}
        </button>
      ))}
    </nav>
  );
}
