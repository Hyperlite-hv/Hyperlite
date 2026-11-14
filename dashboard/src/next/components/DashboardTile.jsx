import { useEffect, useMemo, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { fetchAlertRules, fetchAvailabilitySummary } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useT } from "../i18n";
import { runQuery } from "../lib/query";
import { groupBy, openObject, useQueryObjects } from "../lib/queryObjects";
import { pct, STATE_TONE } from "../pages/AvailabilityPage";
import ExplorerChart from "./ExplorerChart";
import { Pill } from "./ui";

// One read a minute shared by every tile that needs it (several tiles on one dashboard, Home and the page).
const caches = new Map();
function shared(key, load) {
  const hit = caches.get(key);
  if (hit && Date.now() - hit.at < 60000) return hit.promise;
  const promise = load().catch(() => []);
  caches.set(key, { at: Date.now(), promise });
  return promise;
}
function useShared(key, load) {
  const [value, setValue] = useState(null);
  useEffect(() => {
    let live = true;
    const get = () => shared(key, load).then((v) => { if (live) setValue(Array.isArray(v) ? v : []); });
    get();
    const id = setInterval(get, 60000);
    return () => { live = false; clearInterval(id); };
  }, [key, load]);
  return value;
}

function QueryTile({ config }) {
  const t = useT();
  const navigateTo = useInfraStore((s) => s.navigateTo);
  const objects = useQueryObjects();
  const { found, errors } = useMemo(() => runQuery(objects, config.query || ""), [objects, config.query]);
  if (errors.length) return <p className="nx-err">{t("qs.unknown", { terms: errors.join(", ") })}</p>;
  if (config.view === "count") return <p className="hc-tile-big">{found.length}<small>{t("dsh.objects")}</small></p>;
  if (config.view === "bars") {
    const rows = groupBy(found, config.group_by || "state").slice(0, 8);
    const max = Math.max(1, ...rows.map((r) => r.n));
    return (
      <ul className="hc-tile-bars" aria-label={t("dsh.byField", { field: t(`sq.by.${config.group_by || "state"}`) })}>
        {rows.map((r) => <li key={r.key}><span>{r.key}</span><i style={{ width: `${(r.n / max) * 100}%` }} aria-hidden="true" /><b>{r.n}</b></li>)}
        {!rows.length && <li className="nx-muted">{t("qs.none")}</li>}
      </ul>
    );
  }
  return (
    <ul className="hc-tile-list">
      {found.slice(0, 8).map((o) => <li key={`${o.kind}:${o.node}:${o.name}`}><button type="button" className="nx-lnk nx-mono" onClick={() => openObject(navigateTo, o)}>{o.name}</button><small>{o.state}</small></li>)}
      {found.length > 8 && <li className="nx-muted">{t("dsh.more", { n: found.length - 8 })}</li>}
      {!found.length && <li className="nx-muted">{t("qs.none")}</li>}
    </ul>
  );
}

function AvailabilityTile({ config }) {
  const t = useT();
  const navigateTo = useInfraStore((s) => s.navigateTo);
  const rows = useShared("availability", fetchAvailabilitySummary);
  if (!rows) return <p className="nx-muted">{t("loading")}</p>;
  const wanted = config.items?.length ? config.items : null;
  const pick = wanted
    ? wanted.map((w) => rows.find((r) => r.kind === w.kind && (r.name === w.name || (w.kind === "node" && w.name === "local" && r.target === "host")) && (w.kind === "node" || !w.node || w.node === "local" || r.node === w.node))).filter(Boolean)
    : [...rows].sort((a, b) => (a.available_pct ?? 101) - (b.available_pct ?? 101)).slice(0, 8);
  if (!pick.length) return <p className="nx-muted">{t("dsh.noData")}</p>;
  return (
    <ul className="hc-tile-list">
      {pick.map((r) => (
        <li key={r.target}>
          <button type="button" className="nx-lnk nx-mono" onClick={() => (r.kind === "vm" ? navigateTo("vm", r.name, "availability") : navigateTo("node", r.target === "host" ? "local" : r.name, "availability"))}>{r.name}</button>
          <Pill tone={STATE_TONE[r.now.state] || "offline"}>{t(`av.state.${r.now.state}`)}</Pill>
          <b className="nx-mono">{pct(r.available_pct)}{r.available_pct == null ? "" : " %"}</b>
        </li>
      ))}
    </ul>
  );
}

function AlertsTile() {
  const t = useT();
  const rules = useShared("alerts", fetchAlertRules);
  if (!rules) return <p className="nx-muted">{t("loading")}</p>;
  const firing = rules.filter((r) => r.enabled && r.firing?.length);
  if (!firing.length) return <p className="hc-tile-ok">{t("dsh.noAlert")}</p>;
  return <ul className="hc-tile-list">{firing.map((r) => <li key={r.id}><span className="nx-tone-danger">{r.name}</span><small>{(r.firing_labels || []).slice(0, 3).join(", ")}</small></li>)}</ul>;
}

function ResourcesTile() {
  const t = useT();
  const { vms, nodes } = useInfraStore(useShallow((s) => ({ vms: s.vms, nodes: s.nodes })));
  const running = vms.filter((v) => v.state === "running").length;
  const online = nodes.filter((n) => n.state === "online").length;
  return (
    <dl className="hc-tile-kv">
      <div><dt>{t("dsh.vmsRunning")}</dt><dd>{running} / {vms.length}</dd></div>
      <div><dt>{t("dsh.nodesOnline")}</dt><dd>{online} / {nodes.length}</dd></div>
    </dl>
  );
}

export function TileBody({ tile }) {
  const t = useT();
  const c = tile.config || {};
  if (tile.type === "chart") return c.series?.length ? <ExplorerChart chart={c} height={170} label={tile.title} /> : <p className="nx-muted">{t("dsh.noData")}</p>;
  if (tile.type === "query") return <QueryTile config={c} />;
  if (tile.type === "availability") return <AvailabilityTile config={c} />;
  if (tile.type === "alerts") return <AlertsTile />;
  if (tile.type === "resources") return <ResourcesTile />;
  if (tile.type === "note") return <p className="hc-tile-note">{c.text || ""}</p>;
  return null;
}
