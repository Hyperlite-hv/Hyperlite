import { useCallback, useEffect, useState } from "react";
import {
  fetchAutoUpdates, setAutoUpdates, fetchUpdateChannel, setUpdateChannel,
  fetchHostUpdates, upgradeHostPackages, fetchHostDns, setHostDns, fetchHostTime, fetchHostTimezones, setHostTime, fetchHostSyslog, setHostSyslog,
} from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT, useLangStore } from "../i18n";
import { capabilities } from "../lib/capabilities";
import { errorMessage } from "../lib/errors";
import { formatDateTime } from "../lib/format";
import { EmptyState, ErrorState } from "../components/States";
import PermissionNotice from "../components/PermissionNotice";
import { Card, Chip, Field, Loading, TableWrap } from "../components/ui";

const IP = /^((25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}|[0-9a-fA-F:]*:[0-9a-fA-F:.]+)$/;
const HOST = /^(?=.{1,253}$)[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$/;
const words = (s) => s.split(/[\s,]+/).map((x) => x.trim()).filter(Boolean);

function useLocalAdmin(node) {
  const isAdmin = capabilities(useAuthStore((s) => s.role)).admin;
  return { local: node?.id === "local", isAdmin };
}

// Package updates of this node. The upgrade runs as a task (its output in the task's log); Hyperlite's own package
// is updated from its update page, never here.
export function NodeUpdatesPage({ resource: node }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const pushToast = useInfraStore((s) => s.pushToast);
  const navigateTo = useInfraStore((s) => s.navigateTo);
  const { local, isAdmin } = useLocalAdmin(node);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [only, setOnly] = useState(false);
  const load = useCallback(async (refresh = false) => {
    setBusy(refresh);
    try { setData(await fetchHostUpdates(refresh)); setError(null); } catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  }, []);
  useEffect(() => { if (local && isAdmin) load(); }, [load, local, isAdmin]);
  if (!local) return <EmptyState title={t("nn.remoteTitle", { name: node.name })} help={t("nn.remoteHelp")} />;
  if (!isAdmin) return <PermissionNotice requires={t("top.role.admin")} />;
  if (error && !data) return <ErrorState message={error} onRetry={() => load()} />;
  if (!data) return <Loading />;
  if (!data.available) return <Card title={t("hu.title")}><p className="nx-muted" style={{ margin: 0 }}>{t("hu.noApt")}</p></Card>;

  const own = data.packages.find((p) => p.name === "hyperlite");
  const list = data.packages.filter((p) => p.name !== "hyperlite" && (!only || p.security));
  const security = data.packages.filter((p) => p.security).length;
  async function upgrade() {
    const names = list.map((p) => p.name);
    if (!(await confirmAction({ title: t("hu.confirmTitle", { n: names.length }), message: t("hu.confirmMsg"), confirmLabel: t("hu.upgrade") }))) return;
    try {
      await upgradeHostPackages(names);
      pushToast({ kind: "success", title: t("hu.started"), message: t("hu.startedMsg") });
      navigateTo("node", node.id, "tasks");
    } catch (e) { pushToast({ kind: "error", title: t("hu.failed"), message: errorMessage(e) }); }
  }
  return (
    <>
      {data.restart_required && (
        <div className="nx-pending" role="status" style={{ margin: "0 0 var(--space-3)" }}>
          {t("hu.reboot", { pkgs: data.packages_needing_restart.join(", ") || "—" })}
        </div>
      )}
      <Card title={t("hu.title")} note={data.refreshed_at ? t("hu.refreshed", { d: formatDateTime(data.refreshed_at, lang) }) : null} flush actions={<>
        <label className="nx-check"><input type="checkbox" checked={only} onChange={(e) => setOnly(e.target.checked)} /> {t("hu.onlySecurity", { n: security })}</label>
        <button type="button" className="nx-btn" disabled={busy} onClick={() => load(true)}>{busy ? t("hu.refreshing") : t("hu.refresh")}</button>
        <button type="button" className="nx-btn nx-btn--primary" disabled={busy || list.length === 0} onClick={upgrade}>{t("hu.upgradeN", { n: list.length })}</button>
      </>}>
        {own && <p className="nx-muted" style={{ margin: "var(--space-3) var(--space-4) 0", fontSize: "var(--fs-13)" }}>{t("hu.own", { v: own.version })}</p>}
        {list.length === 0 ? <p className="nx-muted" style={{ margin: "var(--space-4)" }}>{t("hu.upToDate")}</p> : (
          <TableWrap label={t("hu.title")}>
            <table className="nx-table">
              <thead><tr><th scope="col">{t("hu.package")}</th><th scope="col">{t("hu.installed")}</th><th scope="col">{t("hu.available")}</th><th scope="col"><span className="nx-sr">{t("hu.kind")}</span></th></tr></thead>
              <tbody>{list.map((p) => (
                <tr key={p.name}><th scope="row" className="nx-mono">{p.name}</th><td className="nx-mono nx-muted">{p.since}</td><td className="nx-mono">{p.version}</td><td>{p.security && <Chip tone="warning">{t("hu.security")}</Chip>}</td></tr>
              ))}</tbody>
            </table>
          </TableWrap>
        )}
      </Card>
      <AutoUpdatesCard />
      <ChannelCard />
    </>
  );
}

// Debian's security updates every night (docs/design/updates-1.0.md): never a reboot, never Hyperlite itself.
function AutoUpdatesCard() {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const [d, setD] = useState(null);
  const [f, setF] = useState({ active: true, time: "03:30" });
  const [error, setError] = useState(null);
  const fill = (x) => { setD(x); setF({ active: x.active, time: x.time }); };
  const load = useCallback(() => fetchAutoUpdates().then((x) => { fill(x); setError(null); }).catch((e) => setError(errorMessage(e))), []);
  useEffect(() => { load(); }, [load]);
  if (error && !d) return <Card title={t("au2.title")}><ErrorState message={error} onRetry={load} /></Card>;
  if (!d) return <Card title={t("au2.title")}><Loading /></Card>;
  const dirty = f.active !== d.active || f.time !== d.time;
  async function save() {
    try { fill(await setAutoUpdates(f.active, f.time)); pushToast({ kind: "success", title: t("hs.saved") }); }
    catch (e) { pushToast({ kind: "error", title: t("hs.saveFailed"), message: errorMessage(e) }); }
  }
  return (
    <Card title={t("au2.title")} actions={<button type="button" className="nx-btn nx-btn--primary" disabled={!dirty || !/^\d\d:\d\d$/.test(f.time)} onClick={save}>{t("hs.save")}</button>}>
      <p className="nx-muted" style={{ margin: "0 0 var(--space-3)", fontSize: "var(--fs-13)" }}>{t("au2.help")}</p>
      {!d.installed && <p className="nx-pending" role="status">{t("au2.notInstalled")}</p>}
      <label className="nx-check"><input type="checkbox" checked={f.active} onChange={(e) => setF({ ...f, active: e.target.checked })} /> {t("au2.enable")}</label>
      <div className="nx-fg" style={{ marginTop: "var(--space-3)" }}>
        <Field label={t("au2.time")} hint={t("au2.timeHint")}>{(p) => <input {...p} type="time" className="nx-inp" disabled={!f.active} value={f.time} onChange={(e) => setF({ ...f, time: e.target.value })} />}</Field>
      </div>
    </Card>
  );
}

function ChannelCard() {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const [d, setD] = useState(null);
  const [f, setF] = useState("stable");
  const [error, setError] = useState(null);
  const fill = (x) => { setD(x); setF(x.channel ?? "stable"); };
  const load = useCallback(() => fetchUpdateChannel().then((x) => { fill(x); setError(null); }).catch((e) => setError(errorMessage(e))), []);
  useEffect(() => { load(); }, [load]);
  if (error && !d) return <Card title={t("ch.title")}><ErrorState message={error} onRetry={load} /></Card>;
  if (!d) return <Card title={t("ch.title")}><Loading /></Card>;
  if (!d.channel) return <Card title={t("ch.title")}><p className="nx-muted" style={{ margin: 0 }}>{t("ch.none")}</p></Card>;
  async function save() {
    try { fill(await setUpdateChannel(f)); pushToast({ kind: "success", title: t("hs.saved") }); }
    catch (e) { pushToast({ kind: "error", title: t("hs.saveFailed"), message: errorMessage(e) }); }
  }
  return (
    <Card title={t("ch.title")} actions={<button type="button" className="nx-btn nx-btn--primary" disabled={f === d.channel} onClick={save}>{t("hs.save")}</button>}>
      <fieldset style={{ border: 0, margin: 0, padding: 0 }}>
        <legend className="nx-sr">{t("ch.title")}</legend>
        {d.channels.map((c) => (
          <label key={c} className="nx-check" style={{ alignItems: "flex-start", marginBottom: "var(--space-2)" }}>
            <input type="radio" name="update-channel" value={c} checked={f === c} onChange={() => setF(c)} />
            <span><strong>{t(`ch.${c}`)}</strong><br /><span className="nx-muted" style={{ fontSize: "var(--fs-13)" }}>{t(`ch.${c}Help`)}</span></span>
          </label>
        ))}
      </fieldset>
      {d.channel === "test" && f === "stable" && <p className="nx-banner" role="status">{t("ch.leaveTest")}</p>}
    </Card>
  );
}

function DnsCard() {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const [d, setD] = useState(null);
  const [f, setF] = useState({ servers: "", search: "" });
  const [error, setError] = useState(null);
  const fill = (x) => { setD(x); setF({ servers: x.servers.join(" "), search: x.search.join(" ") }); };
  const load = useCallback(() => fetchHostDns().then((x) => { fill(x); setError(null); }).catch((e) => setError(errorMessage(e))), []);
  useEffect(() => { load(); }, [load]);
  if (error && !d) return <Card title={t("hs.dns")}><ErrorState message={error} onRetry={load} /></Card>;
  if (!d) return <Card title={t("hs.dns")}><Loading /></Card>;
  const servers = words(f.servers);
  const bad = { servers: servers.length === 0 || servers.length > 3 || servers.some((s) => !IP.test(s)), search: words(f.search).some((s) => !HOST.test(s)) };
  const dirty = f.servers !== d.servers.join(" ") || f.search !== d.search.join(" ");
  async function save() {
    try { fill(await setHostDns({ servers: servers, search: words(f.search) })); pushToast({ kind: "success", title: t("hs.saved") }); }
    catch (e) { pushToast({ kind: "error", title: t("hs.saveFailed"), message: errorMessage(e) }); }
  }
  return (
    <Card title={t("hs.dns")} note={t(`hs.dnsBy.${d.managed_by}`)}>
      {!d.modifiable && <p className="nx-muted" style={{ margin: "0 0 var(--space-3)", fontSize: "var(--fs-13)" }}>{t("hs.dnsReadOnly")}</p>}
      <div className="nx-fg">
        <Field label={t("hs.dnsServers")} hint={t("hs.dnsServersHint")} error={d.modifiable && bad.servers ? t("hs.dnsServersRule") : null}>{(p) => <input {...p} className="nx-inp nx-mono" disabled={!d.modifiable} value={f.servers} onChange={(e) => setF((x) => ({ ...x, servers: e.target.value }))} />}</Field>
        <Field label={t("hs.dnsSearch")} help={t("hlp.dnsSearch")} error={d.modifiable && bad.search ? t("hs.dnsSearchRule") : null}>{(p) => <input {...p} className="nx-inp nx-mono" disabled={!d.modifiable} value={f.search} onChange={(e) => setF((x) => ({ ...x, search: e.target.value }))} />}</Field>
      </div>
      {d.modifiable && <div className="nx-fa"><button type="button" className="nx-btn nx-btn--primary" disabled={!dirty || bad.servers || bad.search} onClick={save}>{t("hs.save")}</button></div>}
    </Card>
  );
}

function TimeCard() {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const [d, setD] = useState(null);
  const [zones, setZones] = useState([]);
  const [f, setF] = useState(null);
  const [error, setError] = useState(null);
  const fill = (x) => { setD(x); setF({ timezone: x.timezone || "", ntp: x.ntp, servers: x.ntp_servers.join(" ") }); };
  const load = useCallback(() => Promise.all([fetchHostTime(), fetchHostTimezones().catch(() => [])])
    .then(([x, z]) => { fill(x); setZones(z); setError(null); }).catch((e) => setError(errorMessage(e))), []);
  useEffect(() => { load(); }, [load]);
  if (error && !d) return <Card title={t("hs.time")}><ErrorState message={error} onRetry={load} /></Card>;
  if (!d) return <Card title={t("hs.time")}><Loading /></Card>;
  const servers = words(f.servers);
  const badServers = servers.some((s) => !IP.test(s) && !HOST.test(s));
  const dirty = f.timezone !== (d.timezone || "") || f.ntp !== d.ntp || f.servers !== d.ntp_servers.join(" ");
  async function save() {
    try {
      fill(await setHostTime({ timezone: f.timezone !== d.timezone ? f.timezone : null, ntp: f.ntp !== d.ntp ? f.ntp : null, ntp_servers: d.timesyncd ? servers : null }));
      pushToast({ kind: "success", title: t("hs.saved") });
    } catch (e) { pushToast({ kind: "error", title: t("hs.saveFailed"), message: errorMessage(e) }); }
  }
  return (
    <Card title={t("hs.time")} note={d.synced ? t("hs.synced") : t("hs.notSynced")}>
      <div className="nx-fg">
        <Field label={t("hs.timezone")}>{(p) => <select {...p} className="nx-inp" value={f.timezone} onChange={(e) => setF((x) => ({ ...x, timezone: e.target.value }))}>
          {(zones.includes(f.timezone) ? zones : [f.timezone, ...zones]).map((z) => <option key={z} value={z}>{z}</option>)}
        </select>}</Field>
        {d.timesyncd && <Field label={t("hs.ntpServers")} hint={t("hs.ntpServersHint")} error={badServers ? t("hs.ntpServersRule") : null}>{(p) => <input {...p} className="nx-inp nx-mono" value={f.servers} onChange={(e) => setF((x) => ({ ...x, servers: e.target.value }))} />}</Field>}
      </div>
      <label className="nx-check"><input type="checkbox" checked={f.ntp} onChange={(e) => setF((x) => ({ ...x, ntp: e.target.checked }))} /> {t("hs.ntp")}</label>
      <div className="nx-fa"><button type="button" className="nx-btn nx-btn--primary" disabled={!dirty || badServers} onClick={save}>{t("hs.save")}</button></div>
    </Card>
  );
}

function SyslogCard() {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const [d, setD] = useState(null);
  const [f, setF] = useState({ host: "", port: "514", protocol: "udp" });
  const [error, setError] = useState(null);
  const fill = (x) => { setD(x); setF({ host: x.target?.host || "", port: String(x.target?.port || 514), protocol: x.target?.protocol || "udp" }); };
  const load = useCallback(() => fetchHostSyslog().then((x) => { fill(x); setError(null); }).catch((e) => setError(errorMessage(e))), []);
  useEffect(() => { load(); }, [load]);
  if (error && !d) return <Card title={t("hs.syslog")}><ErrorState message={error} onRetry={load} /></Card>;
  if (!d) return <Card title={t("hs.syslog")}><Loading /></Card>;
  if (!d.available) return <Card title={t("hs.syslog")}><p className="nx-muted" style={{ margin: 0 }}>{t("hs.noRsyslog")}</p></Card>;
  const port = Number(f.port);
  const bad = { host: f.host !== "" && !IP.test(f.host) && !HOST.test(f.host), port: !Number.isInteger(port) || port < 1 || port > 65535 };
  const dirty = f.host !== (d.target?.host || "") || port !== (d.target?.port || 514) || f.protocol !== (d.target?.protocol || "udp");
  async function save() {
    try { fill(await setHostSyslog({ host: f.host || null, port, protocol: f.protocol })); pushToast({ kind: "success", title: t("hs.saved") }); }
    catch (e) { pushToast({ kind: "error", title: t("hs.saveFailed"), message: errorMessage(e) }); }
  }
  return (
    <Card title={t("hs.syslog")} note={d.target ? t("hs.syslogOn") : t("hs.syslogOff")}>
      <div className="nx-fg">
        <Field label={t("hs.syslogHost")} hint={t("hs.syslogHostHint")} error={bad.host ? t("hs.syslogHostRule") : null}>{(p) => <input {...p} className="nx-inp nx-mono" value={f.host} placeholder="logs.example.org" onChange={(e) => setF((x) => ({ ...x, host: e.target.value.trim() }))} />}</Field>
        <Field label={t("hs.syslogPort")} error={bad.port ? t("hs.syslogPortRule") : null}>{(p) => <input {...p} className="nx-inp nx-mono" inputMode="numeric" value={f.port} onChange={(e) => setF((x) => ({ ...x, port: e.target.value.trim() }))} />}</Field>
        <Field label={t("hs.syslogProto")} help={t("hlp.syslogProto")}>{(p) => <select {...p} className="nx-inp" value={f.protocol} onChange={(e) => setF((x) => ({ ...x, protocol: e.target.value }))}><option value="udp">UDP</option><option value="tcp">TCP</option></select>}</Field>
      </div>
      <div className="nx-fa"><button type="button" className="nx-btn nx-btn--primary" disabled={!dirty || bad.host || bad.port} onClick={save}>{t("hs.save")}</button></div>
    </Card>
  );
}

// DNS resolvers, time and remote syslog of this node, on its System page.
export function HostSettingsCards() {
  return (
    <div className="nx-cols2 nx-cols2--even">
      <DnsCard />
      <TimeCard />
      <SyslogCard />
    </div>
  );
}
