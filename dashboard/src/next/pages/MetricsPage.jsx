import { useCallback, useEffect, useState } from "react";
import { Activity, Plus, Trash2 } from "lucide-react";
import { fetchMetricServers, createMetricServer, updateMetricServer, deleteMetricServer, testMetricServer } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT, useLangStore } from "../i18n";
import { errorMessage } from "../lib/errors";
import { formatDateTime } from "../lib/format";
import { ErrorState } from "../components/States";
import { Card, Chip, Empty, Field, Loading, SideDrawer, TableWrap } from "../components/ui";

const EMPTY = { id: null, nom: "", type: "influxdb", actif: true, url: "", org: "", bucket: "", jeton: "", hote: "", port: "2003", prefixe: "hyperlite" };

// Where this cluster's figures go: the Prometheus endpoint any scraper can read with an API token, and the servers
// the collector pushes to after each sample (InfluxDB 2, Graphite).
export default function MetricsPage() {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const pushToast = useInfraStore((s) => s.pushToast);
  const [servers, setServers] = useState(null);
  const [error, setError] = useState(null);
  const [form, setForm] = useState(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try { setServers(await fetchMetricServers()); setError(null); } catch (e) { setError(errorMessage(e)); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const origin = typeof window !== "undefined" ? window.location.origin : "";
  const host = typeof window !== "undefined" ? window.location.host : "";
  const https = typeof window === "undefined" || window.location.protocol === "https:";
  const scrape = `scrape_configs:\n  - job_name: hyperlite\n    scheme: ${https ? "https" : "http"}\n${https ? "    tls_config: { insecure_skip_verify: true }  # self-signed certificate\n" : ""}    authorization: { credentials: "hlt_…" }      # an API token\n    static_configs:\n      - targets: ["${host}"]`;

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value }));
  const port = Number(form?.port);
  const invalid = !form || !form.nom.trim() || (form.type === "influxdb"
    ? !/^https?:\/\/[^\s/?#]+/.test(form.url) || !form.org.trim() || !form.bucket.trim() || (!form.id && !form.jeton)
    : !form.hote.trim() || !Number.isInteger(port) || port < 1 || port > 65535);
  async function save() {
    setBusy(true);
    const payload = { nom: form.nom.trim(), type: form.type, actif: form.actif, ...(form.type === "influxdb"
      ? { url: form.url.trim(), org: form.org.trim(), bucket: form.bucket.trim(), jeton: form.jeton || null }
      : { hote: form.hote.trim(), port, prefixe: form.prefixe.trim() || "hyperlite" }) };
    try {
      await (form.id ? updateMetricServer(form.id, payload) : createMetricServer(payload));
      pushToast({ kind: "success", title: t("mx.saved"), message: payload.nom });
      setForm(null); await load();
    } catch (e) { pushToast({ kind: "error", title: t("mx.saveFailed"), message: errorMessage(e) }); } finally { setBusy(false); }
  }
  async function test(s) {
    try { const r = await testMetricServer(s.id); pushToast({ kind: "success", title: t("mx.testOk"), message: t("mx.testOkMsg", { n: r.envoyes, name: s.nom }) }); }
    catch (e) { pushToast({ kind: "error", title: t("mx.testFailed"), message: errorMessage(e) }); }
    load();
  }
  async function remove(s) {
    if (!(await confirmAction({ title: t("mx.deleteTitle", { name: s.nom }), message: t("mx.deleteMsg"), confirmLabel: t("vx.delete"), danger: true }))) return;
    try { await deleteMetricServer(s.id); await load(); } catch (e) { pushToast({ kind: "error", title: t("mx.deleteFailed"), message: errorMessage(e) }); }
  }
  const edit = (s) => setForm({ ...EMPTY, ...s, jeton: "", port: String(s.port || 2003), url: s.url || "", org: s.org || "", bucket: s.bucket || "", hote: s.hote || "" });
  const target = (s) => (s.type === "influxdb" ? `${s.url} · ${s.org}/${s.bucket}` : `${s.hote}:${s.port} · ${s.prefixe}`);

  return (
    <>
      <Card title={t("mx.prometheus")}>
        <p className="nx-muted" style={{ margin: "0 0 var(--space-3)", fontSize: "var(--fs-13)" }}>{t("mx.promHelp")}</p>
        <dl className="nx-dl2">
          <dt>{t("mx.endpoint")}</dt><dd className="nx-mono">{origin}/metrics</dd>
          <dt>{t("mx.auth")}</dt><dd>{t("mx.authHelp")}</dd>
        </dl>
        <pre className="nx-code" aria-label={t("mx.scrapeConfig")}>{scrape}</pre>
      </Card>
      <Card title={t("mx.servers")} flush actions={<button type="button" className="nx-btn nx-btn--primary" onClick={() => setForm({ ...EMPTY })}><Plus size={15} aria-hidden="true" />{t("mx.add")}</button>}>
        {error && !servers ? <ErrorState message={error} onRetry={load} /> : !servers ? <Loading style={{ padding: "var(--space-4)" }} /> : servers.length === 0 ? <Empty icon={Activity} title={t("mx.none")} text={t("mx.noneHelp")} /> : (
          <TableWrap label={t("mx.servers")}>
            <table className="nx-table">
              <thead><tr><th scope="col">{t("mx.name")}</th><th scope="col">{t("mx.type")}</th><th scope="col">{t("mx.target")}</th><th scope="col">{t("mx.status")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
              <tbody>{servers.map((s) => (
                <tr key={s.id}>
                  <th scope="row">{s.nom}{!s.actif && <> <Chip>{t("mx.paused")}</Chip></>}</th>
                  <td>{s.type === "influxdb" ? "InfluxDB 2" : "Graphite"}</td>
                  <td className="nx-mono nx-wrapcell">{target(s)}</td>
                  <td className="nx-wrapcell">{s.derniere_erreur ? <span className="nx-danger" title={s.derniere_erreur}>{t("mx.error", { e: s.derniere_erreur })}</span>
                    : s.dernier_envoi ? <span className="nx-muted">{t("mx.lastSent", { d: formatDateTime(s.dernier_envoi, lang) })}</span> : <span className="nx-muted">{t("mx.notYet")}</span>}</td>
                  <td><div className="nx-ra">
                    <button type="button" className="nx-btn nx-btn--ghost" aria-label={t("mx.testX", { name: s.nom })} onClick={() => test(s)}>{t("mx.test")}</button>
                    <button type="button" className="nx-btn nx-btn--ghost" aria-label={t("mx.editX", { name: s.nom })} onClick={() => edit(s)}>{t("mx.edit")}</button>
                    <button type="button" className="nx-btn nx-btn--ghost nx-btn--icon" aria-label={t("mx.deleteX", { name: s.nom })} onClick={() => remove(s)}><Trash2 size={15} aria-hidden="true" /></button>
                  </div></td>
                </tr>
              ))}</tbody>
            </table>
          </TableWrap>
        )}
      </Card>
      <SideDrawer open={!!form} title={form?.id ? t("mx.editTitle") : t("mx.add")} onClose={() => setForm(null)} busy={busy} footer={<>
        <button type="button" className="nx-btn nx-btn--ghost" onClick={() => setForm(null)}>{t("action.cancel")}</button>
        <button type="button" className="nx-btn nx-btn--primary" disabled={invalid || busy} onClick={save}>{t("mx.save")}</button>
      </>}>
        {form && <>
          <div className="nx-fg">
            <Field label={t("mx.name")}>{(p) => <input {...p} className="nx-inp" value={form.nom} onChange={set("nom")} />}</Field>
            <Field label={t("mx.type")}>{(p) => <select {...p} className="nx-inp" value={form.type} disabled={!!form.id} onChange={set("type")}><option value="influxdb">InfluxDB 2</option><option value="graphite">Graphite</option></select>}</Field>
          </div>
          {form.type === "influxdb" ? <>
            <Field label={t("mx.url")} hint={t("mx.urlHint")}>{(p) => <input {...p} className="nx-inp nx-mono" value={form.url} placeholder="https://influx.example.org:8086" onChange={set("url")} />}</Field>
            <div className="nx-fg">
              <Field label={t("mx.org")}>{(p) => <input {...p} className="nx-inp nx-mono" value={form.org} onChange={set("org")} />}</Field>
              <Field label={t("mx.bucket")}>{(p) => <input {...p} className="nx-inp nx-mono" value={form.bucket} onChange={set("bucket")} />}</Field>
            </div>
            <Field label={t("mx.token")} hint={form.id ? t("mx.tokenKeep") : t("mx.tokenHint")}>{(p) => <input {...p} type="password" className="nx-inp nx-mono" value={form.jeton} autoComplete="new-password" onChange={set("jeton")} />}</Field>
          </> : <>
            <div className="nx-fg">
              <Field label={t("mx.host")}>{(p) => <input {...p} className="nx-inp nx-mono" value={form.hote} placeholder="graphite.example.org" onChange={set("hote")} />}</Field>
              <Field label={t("mx.port")}>{(p) => <input {...p} className="nx-inp nx-mono" inputMode="numeric" value={form.port} onChange={set("port")} />}</Field>
            </div>
            <Field label={t("mx.prefix")} hint={t("mx.prefixHint")}>{(p) => <input {...p} className="nx-inp nx-mono" value={form.prefixe} onChange={set("prefixe")} />}</Field>
          </>}
          <label className="nx-check"><input type="checkbox" checked={form.actif} onChange={set("actif")} /> {t("mx.active")}</label>
        </>}
      </SideDrawer>
    </>
  );
}
