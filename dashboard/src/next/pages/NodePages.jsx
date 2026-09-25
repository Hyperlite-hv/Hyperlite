import { useCallback, useEffect, useState } from "react";
import { fetchHostMetricsHistory, fetchNodeCapabilitiesById, fetchHostPreflight, fetchNodeCompatibility } from "../../api/client";
import { flattenCapabilities, deriveFeatures, NA } from "../../lib/capabilitiesView";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { useT, useLangStore } from "../i18n";
import { usePolling } from "../lib/polling";
import { capabilities } from "../lib/capabilities";
import { errorMessage } from "../lib/errors";
import { formatSizeGb } from "../lib/format";
import MetricsHistoryCard from "../../components/MetricsHistoryCard";
import KpiTile from "../components/KpiTile";
import StatusIndicator from "../components/StatusIndicator";
import { EmptyState, ErrorState } from "../components/States";
import PermissionNotice from "../components/PermissionNotice";

const asList = (v) => (Array.isArray(v) ? v : []);
const isLocal = (node) => node?.id === "local";

// Host data (kernel, metrics, networks) only exists for the machine running this Hyperlite. For a remote node
// the API does not provide it: say so instead of showing the local host's values under another name.
function RemoteNotice({ node }) {
  const t = useT();
  return <EmptyState title={t("nn.remoteTitle", { name: node.nom })} help={t("nn.remoteHelp")} />;
}

export function NodeSystemPage({ resource: node }) {
  const t = useT();
  const [health, setHealth] = useState(null);
  const [error, setError] = useState(null);
  const [last, setLast] = useState(null);
  const local = isLocal(node);

  useEffect(() => {
    if (!local) return;
    fetch("/health").then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); }).then(setHealth).catch((e) => setError(errorMessage(e)));
  }, [local]);
  usePolling(async () => { const r = asList(await fetchHostMetricsHistory("1h")); setLast(r[r.length - 1] || null); }, 15000, { enabled: local });
  useEffect(() => { if (local) fetchHostMetricsHistory("1h").then((r) => { const l = asList(r); setLast(l[l.length - 1] || null); }).catch(() => {}); }, [local]);

  if (!node) return null;
  if (!local) return <RemoteNotice node={node} />;
  if (error && !health) return <ErrorState message={error} onRetry={() => { setError(null); fetch("/health").then((r) => r.json()).then(setHealth).catch((e) => setError(errorMessage(e))); }} />;

  const facts = health ? [
    [t("nn.kernel"), health.kernel], [t("nn.hypervisor"), `${health.hypervisor} · libvirt ${health.libvirt_version}`], [t("nn.hostname"), health.hostname],
    ["Python / FastAPI / uvicorn", `${health.python_version} / ${health.fastapi_version} / ${health.uvicorn_version ?? "—"}`],
  ] : [];
  const ram = last?.mem_total_mb ? (last.mem_used_mb ?? 0) / last.mem_total_mb : null;

  return (
    <div className="nx-ns">
      <div className="nx-grid nx-grid--4">
        <KpiTile label={t("nn.cpu")} ratio={last?.cpu_pct != null ? last.cpu_pct / 100 : null} sub={last?.cpu_pct != null ? `${last.cpu_pct} %` : undefined} unavailable={last ? undefined : t("ns.notReported")} />
        <KpiTile label={t("nn.ram")} tone="info" ratio={ram} sub={last?.mem_total_mb ? `${Math.round(last.mem_used_mb)} / ${Math.round(last.mem_total_mb)} MB` : undefined} unavailable={ram == null ? t("ns.notReported") : undefined} />
      </div>
      <section className="nx-card" aria-labelledby="nn-sys">
        <div className="nx-cardhead"><h2 id="nn-sys">{t("nn.system")}</h2></div>
        {!health ? <p className="nx-muted" role="status">{t("loading")}</p> : (
          <dl className="nx-dl">{facts.map(([k, v]) => <div key={k} style={{ display: "contents" }}><dt>{k}</dt><dd className="nx-mono">{v}</dd></div>)}</dl>
        )}
      </section>
      <MetricsHistoryCard title={t("nn.history")} fetcher={fetchHostMetricsHistory} />
    </div>
  );
}

export function NodeNetworkPage({ resource: node }) {
  const t = useT();
  const networks = useInfraStore((s) => s.networks);
  if (!node) return null;
  if (!isLocal(node)) return <RemoteNotice node={node} />;
  return (
    <div className="nx-ns">
      <section className="nx-card" aria-labelledby="nn-net">
        <div className="nx-cardhead"><h2 id="nn-net">{t("inv.networks")} <span className="nx-count">{networks.length}</span></h2></div>
        {networks.length === 0 ? <EmptyState title={t("nn.noNetworks")} /> : (
          <div className="nx-tablewrap">
            <table className="nx-table">
              <thead><tr><th scope="col">{t("ct.name")}</th><th scope="col">{t("stor.type")}</th><th scope="col">{t("nn.bridge")}</th><th scope="col">{t("nn.subnet")}</th></tr></thead>
              <tbody>
                {networks.map((n) => (
                  <tr key={n.nom}>
                    <th scope="row" className="nx-mono">{n.nom}</th>
                    <td>{n.type}</td>
                    <td className="nx-mono">{n.pont || <span className="nx-muted">{t("ns.notReported")}</span>}</td>
                    <td className="nx-mono">{n.reseau ? `${n.reseau.adresse}/${n.reseau.masque}` : <span className="nx-muted">{t("nn.noSubnet")}</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

export function NodeDiskPage({ resource: node }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const storagePools = useInfraStore((s) => s.storagePools);
  if (!node) return null;
  const pools = storagePools.filter((p) => p.node === node.id);
  return (
    <div className="nx-ns">
      <section className="nx-card" aria-labelledby="nn-disk">
        <div className="nx-cardhead"><h2 id="nn-disk">{t("inv.storage")} <span className="nx-count">{pools.length}</span></h2></div>
        {pools.length === 0 ? <EmptyState title={t("nn.noPools")} /> : (
          <div className="nx-tablewrap">
            <table className="nx-table">
              <thead><tr><th scope="col">{t("ns.col.state")}</th><th scope="col">{t("stor.pool")}</th><th scope="col">{t("stor.type")}</th><th scope="col" className="nx-num">{t("stor.capacity")}</th><th scope="col" className="nx-num">{t("stor.free")}</th></tr></thead>
              <tbody>
                {pools.map((p) => (
                  <tr key={p.nom}>
                    <td><StatusIndicator kind="pool" wire={p.etat} /></td>
                    <th scope="row" className="nx-mono">{p.nom}</th>
                    <td>{p.type === "netfs" ? "NFS" : p.type}</td>
                    <td className="nx-num nx-mono">{formatSizeGb(p.capacite_go, lang) ?? "—"}</td>
                    <td className="nx-num nx-mono">{formatSizeGb(p.disponible_go, lang) ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

export function NodeShellPage({ resource: node }) {
  const t = useT();
  const caps = capabilities(useAuthStore((s) => s.role));
  if (!node) return null;
  if (!caps.admin) return <PermissionNotice requires="admin" />;
  if (!isLocal(node)) return <RemoteNotice node={node} />;
  return (
    <div className="nx-ns">
      <section className="nx-card" aria-labelledby="nn-shell">
        <div className="nx-cardhead"><h2 id="nn-shell">{t("nn.shell")}</h2></div>
        <p style={{ marginTop: 0, maxWidth: "62ch" }}>{t("nn.shellHelp", { name: node.nom })}</p>
        <p className="nx-notice nx-notice--warning" role="note">{t("nn.shellWarn")}</p>
        <div><button type="button" className="nx-btn nx-btn--primary" onClick={() => window.open("/host-shell", "hyperlite-host-shell", "width=1100,height=750,noopener")}>{t("nn.openWindow")}</button></div>
      </section>
    </div>
  );
}

const FEATURE = {
  actif: { key: "state.active", shape: "dot", tone: "success" },
  limite: { key: "state.limited", shape: "triangle", tone: "warning" },
  inconnu: { key: "state.unknown", shape: "ring", tone: "unknown" },
};
const CHECK = {
  ok: { key: "cp.ok", shape: "check", tone: "success" },
  warning: { key: "cp.warning", shape: "triangle", tone: "warning" },
  disabled: { key: "cp.disabled", shape: "pause", tone: "warning" },
  blocking: { key: "cp.blocking1", shape: "diamond", tone: "danger" },
};

function Checks({ list, showOk, t }) {
  const bad = list.filter((c) => c.statut !== "ok");
  const rows = showOk ? list : bad;
  return (
    <>
      {bad.length === 0 && <p className="nx-notice nx-notice--success" role="status">{t("cp.allPassed")}</p>}
      {rows.length > 0 && (
        <ul className="nx-list nx-list--checks">
          {rows.map((c, i) => (
            <li key={`${c.id}-${i}`}>
              <StatusIndicator override={CHECK[c.statut] || CHECK.warning} />
              <span>{c.message}{c.fonctionnalite && <span className="nx-muted"> — {t("cp.impact")}: {c.fonctionnalite}</span>}{c.action && <span className="nx-muted"> — {t("cp.action")}: {c.action}</span>}</span>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

// What this node can really do (detected, never assumed), the preflight of the local host, and — for a
// remote node — its compatibility with the local host as a migration source.
export function NodeCompatPage({ resource: node }) {
  const t = useT();
  const nodeId = node?.id;
  const local = nodeId === "local";
  const [caps, setCaps] = useState(null);
  const [preflight, setPreflight] = useState(null);
  const [pair, setPair] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [showOk, setShowOk] = useState(false);

  const load = useCallback(async () => {
    if (!nodeId) return;
    setLoading(true); setError(null);
    try {
      const jobs = [fetchNodeCapabilitiesById(nodeId).then(setCaps)];
      if (local) { setPair(null); jobs.push(fetchHostPreflight().then(setPreflight).catch(() => setPreflight(null))); }
      else { setPreflight(null); jobs.push(fetchNodeCompatibility(nodeId).then(setPair).catch(() => setPair(null))); }
      await Promise.all(jobs);
    } catch (e) { setError(errorMessage(e)); } finally { setLoading(false); }
  }, [nodeId, local]);
  useEffect(() => { setCaps(null); load(); }, [load]);

  if (!node) return null;
  if (error && !caps) return <ErrorState message={error} onRetry={load} />;
  const rows = flattenCapabilities(caps);
  const sections = [...new Set(rows.map((r) => r.section))];
  const features = deriveFeatures(caps);

  return (
    <div className="nx-ns">
      <div className="nx-cardhead">
        <p className="nx-muted" style={{ margin: 0 }}>{t("nc.intro")}</p>
        <button type="button" className="nx-btn" disabled={loading} onClick={load}>{loading ? t("loading") : t("action.refresh")}</button>
      </div>
      {!caps && <p className="nx-muted" role="status">{t("cp.detecting")}</p>}

      {features.length > 0 && (
        <section className="nx-card" aria-labelledby="nc-feat">
          <div className="nx-cardhead"><h2 id="nc-feat">{t("nc.features")}</h2></div>
          <ul className="nx-list nx-list--checks">
            {features.map((f) => <li key={f.id}><StatusIndicator override={FEATURE[f.etat] || FEATURE.inconnu} /><span>{f.label}{f.detail && <span className="nx-muted"> — {f.detail}</span>}</span></li>)}
          </ul>
        </section>
      )}

      {pair && (
        <section className="nx-card" aria-labelledby="nc-pair">
          <div className="nx-cardhead"><h2 id="nc-pair">{t("nc.pair")}</h2></div>
          <Checks list={pair.controles || []} showOk={showOk} t={t} />
        </section>
      )}
      {preflight && (
        <section className="nx-card" aria-labelledby="nc-pre">
          <div className="nx-cardhead"><h2 id="nc-pre">{t("nc.preflight")}</h2><span className="nx-muted">{t("nc.preflightCount", preflight.resume.compte)}</span></div>
          <Checks list={preflight.controles || []} showOk={showOk} t={t} />
        </section>
      )}
      {(pair || preflight) && <label className="nx-check"><input type="checkbox" checked={showOk} onChange={(e) => setShowOk(e.target.checked)} /> {t("nc.showOk")}</label>}

      {sections.map((s, i) => (
        <section key={s} className="nx-card" aria-labelledby={`nc-s${i}`}>
          <div className="nx-cardhead"><h2 id={`nc-s${i}`}>{s}</h2></div>
          <dl className="nx-dl">
            {rows.filter((r) => r.section === s).map((r) => <div key={r.key} style={{ display: "contents" }}><dt>{r.label}</dt><dd className={`nx-mono ${r.value === NA ? "nx-muted" : ""}`}>{r.value === NA ? t("ns.notReported") : String(r.value)}</dd></div>)}
          </dl>
        </section>
      ))}
    </div>
  );
}
