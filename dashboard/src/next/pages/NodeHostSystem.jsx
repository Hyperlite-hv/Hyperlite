import { useCallback, useEffect, useState } from "react";
import {
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
  if (!local) return <EmptyState title={t("nn.remoteTitle", { name: node.nom })} help={t("nn.remoteHelp")} />;
  if (!isAdmin) return <PermissionNotice requires={t("top.role.admin")} />;
  if (error && !data) return <ErrorState message={error} onRetry={() => load()} />;
  if (!data) return <Loading />;
  if (!data.disponible) return <Card title={t("hu.title")}><p className="nx-muted" style={{ margin: 0 }}>{t("hu.noApt")}</p></Card>;

  const own = data.paquets.find((p) => p.nom === "hyperlite");
  const list = data.paquets.filter((p) => p.nom !== "hyperlite" && (!only || p.securite));
  const security = data.paquets.filter((p) => p.securite).length;
  async function upgrade() {
    const names = list.map((p) => p.nom);
    if (!(await confirmAction({ title: t("hu.confirmTitle", { n: names.length }), message: t("hu.confirmMsg"), confirmLabel: t("hu.upgrade") }))) return;
    try {
      await upgradeHostPackages(names);
      pushToast({ kind: "success", title: t("hu.started"), message: t("hu.startedMsg") });
      navigateTo("node", node.id, "tasks");
    } catch (e) { pushToast({ kind: "error", title: t("hu.failed"), message: errorMessage(e) }); }
  }
  return (
    <>
      {data.redemarrage_requis && (
        <div className="nx-pending" role="status" style={{ margin: "0 0 var(--space-3)" }}>
          {t("hu.reboot", { pkgs: data.paquets_redemarrage.join(", ") || "—" })}
        </div>
      )}
      <Card title={t("hu.title")} note={data.actualise_le ? t("hu.refreshed", { d: formatDateTime(data.actualise_le, lang) }) : null} flush actions={<>
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
                <tr key={p.nom}><th scope="row" className="nx-mono">{p.nom}</th><td className="nx-mono nx-muted">{p.depuis}</td><td className="nx-mono">{p.version}</td><td>{p.securite && <Chip tone="warning">{t("hu.security")}</Chip>}</td></tr>
              ))}</tbody>
            </table>
          </TableWrap>
        )}
      </Card>
    </>
  );
}

function DnsCard() {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const [d, setD] = useState(null);
  const [f, setF] = useState({ serveurs: "", recherche: "" });
  const [error, setError] = useState(null);
  const fill = (x) => { setD(x); setF({ serveurs: x.serveurs.join(" "), recherche: x.recherche.join(" ") }); };
  const load = useCallback(() => fetchHostDns().then((x) => { fill(x); setError(null); }).catch((e) => setError(errorMessage(e))), []);
  useEffect(() => { load(); }, [load]);
  if (error && !d) return <Card title={t("hs.dns")}><ErrorState message={error} onRetry={load} /></Card>;
  if (!d) return <Card title={t("hs.dns")}><Loading /></Card>;
  const servers = words(f.serveurs);
  const bad = { serveurs: servers.length === 0 || servers.length > 3 || servers.some((s) => !IP.test(s)), recherche: words(f.recherche).some((s) => !HOST.test(s)) };
  const dirty = f.serveurs !== d.serveurs.join(" ") || f.recherche !== d.recherche.join(" ");
  async function save() {
    try { fill(await setHostDns({ serveurs: servers, recherche: words(f.recherche) })); pushToast({ kind: "success", title: t("hs.saved") }); }
    catch (e) { pushToast({ kind: "error", title: t("hs.saveFailed"), message: errorMessage(e) }); }
  }
  return (
    <Card title={t("hs.dns")} note={t(`hs.dnsBy.${d.gere_par}`)}>
      {!d.modifiable && <p className="nx-muted" style={{ margin: "0 0 var(--space-3)", fontSize: "var(--fs-13)" }}>{t("hs.dnsReadOnly")}</p>}
      <div className="nx-fg">
        <Field label={t("hs.dnsServers")} hint={t("hs.dnsServersHint")} error={d.modifiable && bad.serveurs ? t("hs.dnsServersRule") : null}>{(p) => <input {...p} className="nx-inp nx-mono" disabled={!d.modifiable} value={f.serveurs} onChange={(e) => setF((x) => ({ ...x, serveurs: e.target.value }))} />}</Field>
        <Field label={t("hs.dnsSearch")} error={d.modifiable && bad.recherche ? t("hs.dnsSearchRule") : null}>{(p) => <input {...p} className="nx-inp nx-mono" disabled={!d.modifiable} value={f.recherche} onChange={(e) => setF((x) => ({ ...x, recherche: e.target.value }))} />}</Field>
      </div>
      {d.modifiable && <div className="nx-fa"><button type="button" className="nx-btn nx-btn--primary" disabled={!dirty || bad.serveurs || bad.recherche} onClick={save}>{t("hs.save")}</button></div>}
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
  const fill = (x) => { setD(x); setF({ fuseau: x.fuseau || "", ntp: x.ntp, serveurs: x.serveurs_ntp.join(" ") }); };
  const load = useCallback(() => Promise.all([fetchHostTime(), fetchHostTimezones().catch(() => [])])
    .then(([x, z]) => { fill(x); setZones(z); setError(null); }).catch((e) => setError(errorMessage(e))), []);
  useEffect(() => { load(); }, [load]);
  if (error && !d) return <Card title={t("hs.time")}><ErrorState message={error} onRetry={load} /></Card>;
  if (!d) return <Card title={t("hs.time")}><Loading /></Card>;
  const servers = words(f.serveurs);
  const badServers = servers.some((s) => !IP.test(s) && !HOST.test(s));
  const dirty = f.fuseau !== (d.fuseau || "") || f.ntp !== d.ntp || f.serveurs !== d.serveurs_ntp.join(" ");
  async function save() {
    try {
      fill(await setHostTime({ fuseau: f.fuseau !== d.fuseau ? f.fuseau : null, ntp: f.ntp !== d.ntp ? f.ntp : null, serveurs_ntp: d.timesyncd ? servers : null }));
      pushToast({ kind: "success", title: t("hs.saved") });
    } catch (e) { pushToast({ kind: "error", title: t("hs.saveFailed"), message: errorMessage(e) }); }
  }
  return (
    <Card title={t("hs.time")} note={d.synchronise ? t("hs.synced") : t("hs.notSynced")}>
      <div className="nx-fg">
        <Field label={t("hs.timezone")}>{(p) => <select {...p} className="nx-inp" value={f.fuseau} onChange={(e) => setF((x) => ({ ...x, fuseau: e.target.value }))}>
          {(zones.includes(f.fuseau) ? zones : [f.fuseau, ...zones]).map((z) => <option key={z} value={z}>{z}</option>)}
        </select>}</Field>
        {d.timesyncd && <Field label={t("hs.ntpServers")} hint={t("hs.ntpServersHint")} error={badServers ? t("hs.ntpServersRule") : null}>{(p) => <input {...p} className="nx-inp nx-mono" value={f.serveurs} onChange={(e) => setF((x) => ({ ...x, serveurs: e.target.value }))} />}</Field>}
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
  const [f, setF] = useState({ hote: "", port: "514", protocole: "udp" });
  const [error, setError] = useState(null);
  const fill = (x) => { setD(x); setF({ hote: x.cible?.hote || "", port: String(x.cible?.port || 514), protocole: x.cible?.protocole || "udp" }); };
  const load = useCallback(() => fetchHostSyslog().then((x) => { fill(x); setError(null); }).catch((e) => setError(errorMessage(e))), []);
  useEffect(() => { load(); }, [load]);
  if (error && !d) return <Card title={t("hs.syslog")}><ErrorState message={error} onRetry={load} /></Card>;
  if (!d) return <Card title={t("hs.syslog")}><Loading /></Card>;
  if (!d.disponible) return <Card title={t("hs.syslog")}><p className="nx-muted" style={{ margin: 0 }}>{t("hs.noRsyslog")}</p></Card>;
  const port = Number(f.port);
  const bad = { hote: f.hote !== "" && !IP.test(f.hote) && !HOST.test(f.hote), port: !Number.isInteger(port) || port < 1 || port > 65535 };
  const dirty = f.hote !== (d.cible?.hote || "") || port !== (d.cible?.port || 514) || f.protocole !== (d.cible?.protocole || "udp");
  async function save() {
    try { fill(await setHostSyslog({ hote: f.hote || null, port, protocole: f.protocole })); pushToast({ kind: "success", title: t("hs.saved") }); }
    catch (e) { pushToast({ kind: "error", title: t("hs.saveFailed"), message: errorMessage(e) }); }
  }
  return (
    <Card title={t("hs.syslog")} note={d.cible ? t("hs.syslogOn") : t("hs.syslogOff")}>
      <div className="nx-fg">
        <Field label={t("hs.syslogHost")} hint={t("hs.syslogHostHint")} error={bad.hote ? t("hs.syslogHostRule") : null}>{(p) => <input {...p} className="nx-inp nx-mono" value={f.hote} placeholder="logs.example.org" onChange={(e) => setF((x) => ({ ...x, hote: e.target.value.trim() }))} />}</Field>
        <Field label={t("hs.syslogPort")} error={bad.port ? t("hs.syslogPortRule") : null}>{(p) => <input {...p} className="nx-inp nx-mono" inputMode="numeric" value={f.port} onChange={(e) => setF((x) => ({ ...x, port: e.target.value.trim() }))} />}</Field>
        <Field label={t("hs.syslogProto")}>{(p) => <select {...p} className="nx-inp" value={f.protocole} onChange={(e) => setF((x) => ({ ...x, protocole: e.target.value }))}><option value="udp">UDP</option><option value="tcp">TCP</option></select>}</Field>
      </div>
      <div className="nx-fa"><button type="button" className="nx-btn nx-btn--primary" disabled={!dirty || bad.hote || bad.port} onClick={save}>{t("hs.save")}</button></div>
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
