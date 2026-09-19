export default function Tabs({ tabs, active, onChange }) {
  return (
    <div role="tablist" className="flex items-center gap-1 border-b border-anthracite-600 px-4 overflow-x-auto">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          role="tab"
          aria-selected={active === tab.id}
          onClick={() => onChange(tab.id)}
          className={`px-3 py-2.5 text-sm font-medium border-b-2 -mb-px whitespace-nowrap transition-colors ${
            active === tab.id
              ? "border-accent-blue text-anthracite-100"
              : "border-transparent text-anthracite-300 hover:text-anthracite-100"
          }`}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}
