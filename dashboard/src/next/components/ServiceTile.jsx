import { Star } from "lucide-react";
import { useT } from "../i18n";
import { SERVICE, serviceLabel, useConsoleStore } from "../lib/services";

// The coloured tile of a service: its icon on the colour of its family. Decorative: the name is always beside it.
export default function ServiceTile({ id, size = 20 }) {
  const s = SERVICE[id] || SERVICE.all;
  const Icon = s.icon;
  return (
    <span className={`hc-tile hc-t-${s.cat}`} style={{ "--s": `${size / 15}rem` }} aria-hidden="true">
      <Icon size={Math.round(size * 0.6)} strokeWidth={1.9} />
    </span>
  );
}

// The star that pins a service in the left menu.
export function FavoriteButton({ id }) {
  const t = useT();
  const on = useConsoleStore((s) => s.favorites.includes(id));
  const toggle = useConsoleStore((s) => s.toggleFavorite);
  const label = t(on ? "svc.unpin" : "svc.pin", { name: t(serviceLabel(id)) });
  return (
    <button type="button" className={`hc-ibtn hc-star${on ? " is-on" : ""}`} aria-pressed={on} aria-label={label} title={label}
      onClick={(e) => { e.preventDefault(); e.stopPropagation(); toggle(id); }}>
      <Star size={16} aria-hidden="true" />
    </button>
  );
}
