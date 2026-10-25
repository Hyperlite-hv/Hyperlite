import { useEffect, useMemo, useState } from "react";
import { fetchSiteOutgoing, fetchSites, fetchSiteRemotes, fetchSiteList, moveVmToSite } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useT } from "../i18n";
import { errorMessage } from "../lib/errors";
import { Field, Loading, SideDrawer } from "./ui";

const NAME_RE = /^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}$/;
const POOL_TYPES = new Set(["dir", "netfs", "logical"]); // what a target takes in this version (app/core/site_transfer.py)
const mib = (bytes) => `${Math.round(bytes / 1024 ** 2)} MiB`;

// A VM moved to another cluster, as `qm remote-migrate` (docs/design/multi-cluster.md, section 9): stopped, its disks
// sent and it is deleted here; running, live (`--online`, its CPU checked by the target first), it keeps running there.
// a remote this cluster holds a manage token for, one of its online nodes, a pool there for each disk and a network
// for each card (the same name chosen first), and what stays behind said before anything goes. The move is a task;
// the VM is deleted here once the target defined it.
export default function SiteMoveDialog({ vm, onClose }) {
  const t = useT();
  const live = vm.state === "active";
  const pushToast = useInfraStore((s) => s.pushToast);
  const refreshAll = useInfraStore((s) => s.refreshAll);
  const [info, setInfo] = useState(null);
  const [sites, setSites] = useState(null);
  const [remotes, setRemotes] = useState([]);
  const [lists, setLists] = useState(null);
  const [form, setForm] = useState({ remote: "", node: "", pools: [], networks: [], name: vm.name, start: false, limit: "" });
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    fetchSiteOutgoing(vm.name, live).then(setInfo).catch((e) => setInfo({ error: errorMessage(e) }));
    Promise.all([fetchSites(), fetchSiteRemotes()]).then(([s, r]) => { setSites(s); setRemotes(r); }).catch(() => { setSites([]); setRemotes([]); });
  }, [vm.name, live]);

  const usable = remotes.filter((r) => r.uses?.includes("manage"));
  const nodes = useMemo(() => {
    const site = sites?.find((s) => !s.self && s.name === form.remote);
    return (site?.summary?.nodes || []).filter((n) => n.online).map((n) => n.name);
  }, [sites, form.remote]);

  useEffect(() => {
    if (!form.remote || !form.node || !info?.spec) { setLists(null); return undefined; }
    let alive = true;
    setLists({ loading: true });
    Promise.all([fetchSiteList(form.remote, "storage", form.node), fetchSiteList(form.remote, "networks", form.node)])
      .then(([pools, networks]) => {
        if (!alive) return;
        const okPools = (Array.isArray(pools) ? pools : []).filter((p) => POOL_TYPES.has(p.type) && p.state !== "inactive");
        const nets = (Array.isArray(networks) ? networks : []).map((n) => n.name);
        const firstPool = okPools.find((p) => p.name === "default")?.name || okPools[0]?.name || "";
        setLists({ pools: okPools, networks: nets });
        setForm((f) => ({
          ...f,
          pools: info.spec.disks.map(() => firstPool),
          networks: info.spec.nics.map((nic) => (nets.includes(nic.network) ? nic.network : nets[0] || "")),
        }));
      })
      .catch((e) => alive && setLists({ error: errorMessage(e) }));
    return () => { alive = false; };
  }, [form.remote, form.node, info]);

  const set = (k) => (e) => {
    const value = e.target.type === "checkbox" ? e.target.checked : e.target.value;
    setForm((f) => ({ ...f, [k]: value, ...(k === "remote" ? { node: "" } : {}) }));
  };
  const setAt = (k, i) => (e) => setForm((f) => ({ ...f, [k]: f[k].map((v, j) => (j === i ? e.target.value : v)) }));
  const limit = form.limit === "" ? null : Number(form.limit);
  const ready = info?.spec && form.remote && form.node && lists?.pools && NAME_RE.test(form.name)
    && form.pools.length === info.spec.disks.length && form.pools.every(Boolean)
    && form.networks.length === info.spec.nics.length && form.networks.every(Boolean)
    && (limit === null || (Number.isInteger(limit) && limit > 0));

  async function go() {
    setBusy(true);
    try {
      const common = { remote: form.remote, node: form.node, pools: form.pools, networks: form.networks, limit_mib: limit };
      await moveVmToSite(vm.name, live ? { ...common, live: true } : { ...common, name: form.name === vm.name ? null : form.name, start: form.start });
      pushToast({ kind: "success", title: t("smv.started"), message: t("smv.startedMsg", { vm: vm.name, site: `${form.remote}/${form.node}` }) });
      refreshAll?.();
      onClose();
    } catch (e) {
      pushToast({ kind: "error", title: t("smv.failed"), message: errorMessage(e) });
      setBusy(false);
    }
  }

  return (
    <SideDrawer open title={t(live ? "smv.titleLive" : "smv.title", { name: vm.name })} onClose={onClose} busy={busy} footer={<>
      <button type="button" className="nx-btn nx-btn--ghost" onClick={onClose} disabled={busy}>{t("action.cancel")}</button>
      <button type="button" className="nx-btn nx-btn--primary" disabled={!ready || busy} onClick={go}>{busy ? t("smv.starting") : t("smv.go")}</button>
    </>}>
      <p className="nx-muted" style={{ margin: 0 }}>{t(live ? "smv.helpLive" : "smv.help")}</p>
      {!info ? <Loading /> : info.error ? <div className="nx-bn" data-tone="danger" role="alert">{info.error}</div> : <>
        {sites && usable.length === 0 && <div className="nx-bn" data-tone="warning" role="status">{t("smv.noRemote")}</div>}
        <Field label={t("smv.remote")}>{(p) => <select {...p} className="nx-inp" value={form.remote} onChange={set("remote")} disabled={busy}>
          <option value="">{t("sec.choose")}</option>{usable.map((r) => <option key={r.name} value={r.name}>{r.name}</option>)}
        </select>}</Field>
        {form.remote && <Field label={t("smv.node")} hint={nodes.length ? null : t("smv.noNode")}>{(p) => <select {...p} className="nx-inp" value={form.node} onChange={set("node")} disabled={busy}>
          <option value="">{t("sec.choose")}</option>{nodes.map((n) => <option key={n} value={n}>{n}</option>)}
        </select>}</Field>}
        {lists?.loading && <Loading />}
        {lists?.error && <div className="nx-bn" data-tone="danger" role="alert">{lists.error}</div>}
        {lists?.pools && <>
          {lists.pools.length === 0 && <div className="nx-bn" data-tone="warning" role="status">{t("smv.noPool")}</div>}
          {info.spec.disks.map((d, i) => (
            <Field key={d.dev} label={t("smv.diskPool", { dev: d.dev, size: mib(d.size) })} hint={t("smv.allocated", { size: mib(info.allocated[d.dev] || 0) })}>{(p) => (
              <select {...p} className="nx-inp" value={form.pools[i] || ""} onChange={setAt("pools", i)} disabled={busy}>
                {lists.pools.map((pool) => <option key={pool.name} value={pool.name}>{pool.name} · {pool.available_gb} GiB {t("smv.free")}</option>)}
              </select>
            )}</Field>
          ))}
          {info.spec.nics.map((nic, i) => (
            <Field key={nic.mac} label={t("smv.nicNetwork", { mac: nic.mac, network: nic.network || "?" })}>{(p) => (
              <select {...p} className="nx-inp" value={form.networks[i] || ""} onChange={setAt("networks", i)} disabled={busy}>
                {lists.networks.map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
            )}</Field>
          ))}
          <div className="nx-fg">
            {!live && <Field label={t("smv.name")} error={NAME_RE.test(form.name) ? null : t("vx.nameRule")}>{(p) => <input {...p} className="nx-inp" value={form.name} onChange={set("name")} disabled={busy} />}</Field>}
            <Field label={t("smv.limit")} hint={t("smv.limitHint")}>{(p) => <input {...p} className="nx-inp nx-mono" type="number" min={1} value={form.limit} onChange={set("limit")} disabled={busy} />}</Field>
          </div>
          {!live && <label className="nx-check"><input type="checkbox" checked={form.start} onChange={set("start")} disabled={busy} /> {t("smv.start")}</label>}
        </>}
        {info.left?.length > 0 && <div className="nx-bn" data-tone="warning" role="status">
          <strong>{t("smv.left")}</strong>
          <ul style={{ margin: "var(--space-1) 0 0", paddingLeft: "1.2em" }}>{info.left.map((x) => <li key={x}>{x}</li>)}</ul>
        </div>}
        <p className="nx-f-h" style={{ margin: 0 }}>{t(live ? "smv.deleteNoteLive" : "smv.deleteNote")}</p>
      </>}
    </SideDrawer>
  );
}
