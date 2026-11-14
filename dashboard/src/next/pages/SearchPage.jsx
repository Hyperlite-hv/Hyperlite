import { useEffect, useMemo, useState } from "react";
import { Search } from "lucide-react";
import { useShallow } from "zustand/react/shallow";
import { fetchContainers, fetchPools } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useT } from "../i18n";
import { metaOf, useMetaStore } from "../lib/meta";
import { runQuery, useQueryStore } from "../lib/query";
import { TagChips } from "../components/NotesCard";
import { Card, Chip, Empty, PageHeader, TableWrap } from "../components/ui";

const EXAMPLES = ["kind:vm state:stopped", "tag:prod -pool:web", "vcpu>=4 mem>8", "kind:vm os:windows", "name:web-*"];

// Search by query (docs/design/governance.md, section 14): every VM, container and node, narrowed by terms such as
// kind:vm state:stopped tag:prod pool:web vcpu>=4 mem>8 (app: lib/query.js).
export default function SearchPage() {
  const t = useT();
  const { vms, nodes, navigateTo } = useInfraStore(useShallow((s) => ({ vms: s.vms, nodes: s.nodes, navigateTo: s.navigateTo })));
  const byKey = useMetaStore((s) => s.byKey);
  const loadMeta = useMetaStore((s) => s.load);
  const q = useQueryStore((s) => s.q);
  const setQ = useQueryStore((s) => s.setQ);
  const [pools, setPools] = useState([]);
  const [containers, setContainers] = useState([]);
  useEffect(() => { loadMeta(); fetchPools().then(setPools).catch(() => {}); fetchContainers().then(setContainers).catch(() => {}); }, [loadMeta]);
  const objects = useMemo(() => {
    const nodeName = (id) => nodes.find((n) => n.id === id)?.name || id;
    const poolsOf = (name) => pools.filter((p) => p.vms.includes(name) || (p.dynamic_vms || []).includes(name)).map((p) => p.name);
    return [
      ...vms.map((v) => ({ kind: "vm", name: v.name, state: v.state, node: v.node, nodeName: nodeName(v.node), tags: metaOf(byKey, "vm", v.node, v.name)?.tags || [], pools: poolsOf(v.name), vcpu: v.vcpu, memory_mb: v.memory_mb, os: v.os, ip: v.ip, raw: v })),
      ...containers.map((c) => ({ kind: "container", name: c.name, state: c.state, node: "local", nodeName: nodeName("local"), tags: metaOf(byKey, "container", "local", c.name)?.tags || [], pools: [], vcpu: c.vcpu, memory_mb: c.memory_mb, ip: c.ip })),
      ...nodes.map((n) => ({ kind: "node", name: n.name, state: n.state, node: n.id, nodeName: n.name, tags: metaOf(byKey, "node", null, n.id)?.tags || [], pools: [], vcpu: n.cpu_coeurs, memory_mb: n.memoire_totale_mo, ip: n.ip, id: n.id })),
    ];
  }, [vms, nodes, containers, pools, byKey]);
  const { found, errors } = useMemo(() => runQuery(objects, q), [objects, q]);
  const open = (o) => (o.kind === "vm" ? navigateTo("vm", o.name, "summary") : o.kind === "container" ? navigateTo("container", o.name, "summary") : navigateTo("node", o.id, "summary"));
  return (
    <>
      <PageHeader title={t("tab.search")} desc={t("qs.desc")} />
      <label className="hc-filter hc-filter--wide"><Search size={16} aria-hidden="true" /><input type="search" className="nx-mono" value={q} onChange={(e) => setQ(e.target.value)} placeholder="kind:vm state:stopped tag:prod" aria-label={t("tab.search")} autoFocus /></label>
      <p className="nx-muted" style={{ fontSize: "var(--fs-13)" }}>{t("qs.help")} {EXAMPLES.map((ex) => <button key={ex} type="button" className="nx-lnk nx-mono" style={{ marginRight: "var(--space-2)" }} onClick={() => setQ(ex)}>{ex}</button>)}</p>
      {errors.length > 0 && <p className="nx-err" role="alert">{t("qs.unknown", { terms: errors.join(", ") })}</p>}
      <Card flush>
        {!q.trim() ? <Empty icon={Search} title={t("qs.start")} text={t("qs.startText")} /> : found.length === 0 ? <Empty icon={Search} title={t("qs.none")} text="" /> : (
          <TableWrap label={t("tab.search")}>
            <table className="nx-table">
              <thead><tr><th scope="col">{t("pl.name")}</th><th scope="col">{t("qs.kind")}</th><th scope="col">{t("ns.col.state")}</th><th scope="col">{t("acc.node")}</th><th scope="col">{t("pl.members")}</th><th scope="col">{t("pl.tags")}</th></tr></thead>
              <tbody>
                {found.map((o) => (
                  <tr key={`${o.kind}:${o.node}:${o.name}`}>
                    <th scope="row"><button type="button" className="nx-lnk nx-mono" onClick={() => open(o)}>{o.name}</button></th>
                    <td><Chip>{t(`qs.k.${o.kind}`)}</Chip></td>
                    <td>{o.state}</td>
                    <td>{o.nodeName}</td>
                    <td>{o.pools.join(", ") || "—"}</td>
                    <td><TagChips tags={o.tags} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}
      </Card>
      <p className="hc-count" role="status">{q.trim() ? t("qs.count", { n: found.length }) : null}</p>
    </>
  );
}
SearchPage.ownHeader = true;
