import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";

const RANGES = [
  { hours: 1, label: "1h" },
  { hours: 6, label: "6h" },
  { hours: 24, label: "24h" },
];

export default function RangeToggle({ value, onChange }) {
  return (
    <Tabs value={String(value)} onValueChange={(v) => onChange(Number(v))}>
      <TabsList>
        {RANGES.map((r) => (
          <TabsTrigger aria-controls={undefined} key={r.hours} value={String(r.hours)}>{r.label}</TabsTrigger>
        ))}
      </TabsList>
    </Tabs>
  );
}
