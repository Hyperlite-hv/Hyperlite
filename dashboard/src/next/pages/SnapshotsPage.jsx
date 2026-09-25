import { useEffect, useState } from "react";
import { fetchSnapshots } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useT } from "../i18n";

const MAX_VMS = 60; // one request per VM (the API has no cross-VM listing)

// Snapshots of every local VM in one place; restore/delete stay in each VM's Snapshots tab.
export default function SnapshotsPage() {
  const t = useT();
  const vms = useInfraStore((s) => s.vms);
  const navigateTo = useInfraStore((s) => s.navigateTo);
  const [rows, setRows] = useState(null);
  const local = vms.filter((v) => v.node === "local").slice(0, MAX_VMS);
  const key = local.map((v) => v.nom).join("|");

  useEffect(() => {
    let cancelled = false;
    Promise.all(local.map((v) => fetchSnapshots(v.nom).then((s) => (Array.isArray(s) ? s.map((x) => ({ ...x, vm: v.nom })) : [])).catch(() => [])))
      .then((all) => { if (!cancelled) setRows(all.flat()); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return (
    <div className="nx-ns">
      <section className="nx-card" aria-labelledby="snap-h">
        <div className="nx-cardhead"><h2 id="snap-h">{t("nav.snapshots")} <span className="nx-count">{rows ? rows.length : "…"}</span></h2></div>
        {vms.length > local.length && <p className="nx-muted">{t("snap.localOnly")}</p>}
        {rows == null ? <p className="nx-muted">{t("loading")}</p> : rows.length === 0 ? <p className="nx-muted" role="status">{t("snap.none")}</p> : (
          <div className="nx-tablewrap">
            <table className="nx-table">
              <thead><tr><th scope="col">VM</th><th scope="col">{t("ns.col.name")}</th><th scope="col">{t("snap.created")}</th><th scope="col">{t("snap.description")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
              <tbody>
                {rows.map((s) => (
                  <tr key={`${s.vm}:${s.nom}`}>
                    <th scope="row"><button type="button" className="nx-link" onClick={() => navigateTo("vm", s.vm, "summary")}>{s.vm}</button></th>
                    <td className="nx-mono">{s.nom}{s.actuel ? ` · ${t("snap.current")}` : ""}</td>
                    <td className="nx-mono">{s.date_creation || "—"}</td>
                    <td>{s.description || "—"}</td>
                    <td><button type="button" className="nx-btn nx-btn--ghost" onClick={() => navigateTo("vm", s.vm, "snapshots")}>{t("snap.manage")}</button></td>
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
