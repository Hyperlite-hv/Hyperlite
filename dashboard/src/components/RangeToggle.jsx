const RANGES = [
  { hours: 1, label: "1h" },
  { hours: 6, label: "6h" },
  { hours: 24, label: "24h" },
];

export default function RangeToggle({ value, onChange }) {
  return (
    <div className="flex gap-0.5 rounded-md bg-anthracite-700 p-0.5">
      {RANGES.map((r) => (
        <button
          key={r.hours}
          onClick={() => onChange(r.hours)}
          className={`rounded px-2 py-0.5 text-xs font-medium transition-colors ${
            value === r.hours ? "bg-accent-blue text-white" : "text-anthracite-300 hover:text-anthracite-100"
          }`}
        >
          {r.label}
        </button>
      ))}
    </div>
  );
}
