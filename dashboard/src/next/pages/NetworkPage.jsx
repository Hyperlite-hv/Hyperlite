import { Fragment, useCallback, useEffect, useState } from "react";
import { fetchNetworks, fetchNetworkDetail, createNetwork, deleteNetwork, fetchNetworkFirewall, setNetworkFirewall } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT } from "../i18n";
import { capabilities } from "../lib/capabilities";
import { errorMessage } from "../lib/errors";
import StatusIndicator from "../components/StatusIndicator";
import FirewallRulesEditor from "../../components/FirewallRulesEditor";

const PROTECTED = ["default", "hyperlite-isolated"];
const IPV4 = /^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$/;
const EMPTY = { name: "", mode: "isole", subnet_address: "192.168.150.1", dhcp_start: "192.168.150.10", dhcp_end: "192.168.150.100", bridge_name: "" };

function FirewallSection({ name, isAdmin }) {
  const fetchConfig = useCallback(() => fetchNetworkFirewall(name), [name]);
  const saveConfig = useCallback((config) => setNetworkFirewall(name, config), [name]);
  return <FirewallRulesEditor title="Network firewall" fetchConfig={fetchConfig} saveConfig={saveConfig} isAdmin={isAdmin} />;
}

// Virtual networks: list, details (subnet, DHCP leases, firewall), create and delete, with the same API
// calls and protections as the historical screen.
export default function NetworkPage() {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const caps = capabilities(useAuthStore((s) => s.role));
  const [nets, setNets] = useState(null);
  const [open, setOpen] = useState(null);
  const [detail, setDetail] = useState(null);
  const [form, setForm] = useState(EMPTY);
  const [formOpen, setFormOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [touched, setTouched] = useState(false);

  const reload = useCallback(async () => {
    try { const r = await fetchNetworks(); setNets(Array.isArray(r) ? r : []); }
    catch (e) { pushToast({ kind: "error", title: t("net.error"), message: errorMessage(e) }); setNets([]); }
  }, [pushToast, t]);
  useEffect(() => { reload(); }, [reload]);

  async function toggle(name) {
    if (open === name) { setOpen(null); return; }
    setOpen(name); setDetail(null);
    try { setDetail(await fetchNetworkDetail(name)); } catch (e) { pushToast({ kind: "error", title: t("net.detailError"), message: errorMessage(e) }); }
  }

  const bridge = form.mode === "bridge";
  const problems = {
    name: !form.name.trim() ? t("net.nameRequired") : "",
    subnet_address: !bridge && !IPV4.test(form.subnet_address) ? t("net.ipInvalid") : "",
    dhcp_start: !bridge && !IPV4.test(form.dhcp_start) ? t("net.ipInvalid") : "",
    dhcp_end: !bridge && !IPV4.test(form.dhcp_end) ? t("net.ipInvalid") : "",
    bridge_name: bridge && !form.bridge_name.trim() ? t("net.bridgeRequired") : "",
  };
  const invalid = Object.values(problems).some(Boolean);

  async function create(e) {
    e.preventDefault();
    setTouched(true);
    if (invalid) return;
    setBusy(true);
    try {
      await createNetwork({ name: form.name, mode: form.mode, subnet_address: bridge ? undefined : form.subnet_address, dhcp_start: bridge ? undefined : form.dhcp_start, dhcp_end: bridge ? undefined : form.dhcp_end, bridge_name: bridge ? form.bridge_name : undefined });
      pushToast({ kind: "success", title: t("net.created"), message: form.name });
      setFormOpen(false); setForm(EMPTY); setTouched(false); await reload();
    } catch (err) { pushToast({ kind: "error", title: t("stor.createFailed"), message: errorMessage(err) }); }
    finally { setBusy(false); }
  }

  async function remove(name) {
    if (!(await confirmAction({ title: `Delete network '${name}'?`, message: t("net.deleteHelp"), confirmLabel: "Delete", danger: true }))) return;
    try { await deleteNetwork(name); pushToast({ kind: "success", title: t("net.deleted"), message: name }); await reload(); }
    catch (err) { pushToast({ kind: "error", title: t("stor.deleteFailed"), message: errorMessage(err) }); }
  }

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const field = (k, label, aria, placeholder) => (
    <label>{label}<input className="nx-input" aria-label={aria} aria-invalid={touched && !!problems[k]} value={form[k]} onChange={set(k)} placeholder={placeholder} />{touched && problems[k] && <span role="alert" className="nx-hint nx-tone-danger">{problems[k]}</span>}</label>
  );

  return (
    <div className="nx-ns">
      <section className="nx-card" aria-labelledby="net-h">
        <div className="nx-cardhead">
          <h2 id="net-h">{t("net.networks")} <span className="nx-count">{nets ? nets.length : "…"}</span></h2>
          {caps.admin && <button type="button" className="nx-btn" aria-expanded={formOpen} onClick={() => setFormOpen((o) => !o)}>{t("net.create")}</button>}
        </div>
        {formOpen && (
          <form className="nx-form" onSubmit={create} noValidate>
            <div className="nx-formgrid">
              {field("name", t("ns.col.name"), "Name", "isolated-lab")}
              <label>{t("net.mode")}
                <select className="nx-input" aria-label="Network mode" value={form.mode} onChange={set("mode")}>
                  <option value="isole">Isolated (no external access)</option>
                  <option value="nat">NAT (outbound through the host)</option>
                  <option value="bridge">Bridge to an existing physical network</option>
                </select>
              </label>
            </div>
            {bridge ? field("bridge_name", t("net.bridge"), "Host bridge name (e.g. br0)", "br0") : (
              <div className="nx-formgrid nx-formgrid--3">
                {field("subnet_address", t("net.gateway"), "Gateway (e.g. 192.168.150.1)", "192.168.150.1")}
                {field("dhcp_start", t("net.dhcpStart"), "DHCP start", "192.168.150.10")}
                {field("dhcp_end", t("net.dhcpEnd"), "DHCP end", "192.168.150.100")}
              </div>
            )}
            <div className="nx-formactions">
              <button type="button" className="nx-btn" onClick={() => setFormOpen(false)}>{t("action.cancel")}</button>
              <button type="submit" className="nx-btn nx-btn--primary" disabled={busy}>{t("action.create")}</button>
            </div>
          </form>
        )}
        {nets == null ? <p className="nx-muted">{t("loading")}</p> : nets.length === 0 ? <p className="nx-muted" role="status">{t("net.none")}</p> : (
          <div className="nx-tablewrap">
            <table className="nx-table">
              <thead><tr><th scope="col">{t("ns.col.state")}</th><th scope="col">{t("ns.col.name")}</th><th scope="col">{t("stor.type")}</th><th scope="col">{t("net.bridge")}</th><th scope="col">{t("net.subnet")}</th><th scope="col"><span className="nx-sr">{t("actions")}</span></th></tr></thead>
              <tbody>
                {nets.map((n) => (
                  <Fragment key={n.nom}>
                    <tr>
                      <td><StatusIndicator override={{ key: n.actif ? "state.active" : "state.inactive", shape: n.actif ? "dot" : "square", tone: n.actif ? "success" : "offline" }} /></td>
                      <th scope="row"><button type="button" className="nx-link" aria-expanded={open === n.nom} onClick={() => toggle(n.nom)}>{n.nom}</button></th>
                      <td>{n.type}</td><td className="nx-mono">{n.pont || "—"}</td>
                      <td className="nx-mono">{n.reseau ? `${n.reseau.adresse}/${n.reseau.masque}` : "—"}</td>
                      <td className="nx-num">{caps.admin && !PROTECTED.includes(n.nom) && <button type="button" className="nx-btn nx-btn--danger" aria-label={`Delete network ${n.nom}`} onClick={() => remove(n.nom)}>{t("menu.delete").replace("…", "")}</button>}</td>
                    </tr>
                    {open === n.nom && (
                      <tr><td colSpan={6} className="nx-detailcell">
                        {!detail ? <span className="nx-muted">{t("loading")}</span> : (
                          <div className="nx-ns">
                            <div><strong>{t("net.leases")}</strong>
                              {(detail.baux_dhcp || []).length === 0 ? <p className="nx-muted" style={{ margin: "4px 0 0" }}>{t("net.noLeases")}</p> : (
                                <ul className="nx-list nx-list--vols">{detail.baux_dhcp.map((b, i) => <li key={i}><span className="nx-mono">{b.ip}</span><span className="nx-mono nx-muted">{b.mac}</span><span className="nx-muted">{b.hostname || ""}</span></li>)}</ul>
                              )}
                            </div>
                            <FirewallSection name={n.nom} isAdmin={caps.admin} />
                          </div>
                        )}
                      </td></tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
