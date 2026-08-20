const ITEMS = [
  { className: 'fold', label: 'fold' },
  { className: 'call', label: 'call / check' },
  { className: 'raise', label: 'raise' },
  { className: 'allin', label: 'all-in' },
] as const;

export function Legend() {
  return (
    <div className="legend">
      {ITEMS.map(({ className, label }) => (
        <span className="legend-item" key={className}>
          <i className={`swatch ${className}`} />
          {label}
        </span>
      ))}
      <span className="legend-item legend-untrained-note">
        <span className="swatch untrained-swatch" />
        faded = not enough data yet
      </span>
    </div>
  );
}
