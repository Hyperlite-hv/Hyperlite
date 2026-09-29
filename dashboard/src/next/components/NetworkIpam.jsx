import { useState } from "react";
import { editNetwork, addNetworkReservation, deleteNetworkReservation, startNetwork, stopNetwork } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { useT, useLangStore } from "../i18n";
import { errorMessage } from "../lib/errors";
import { formatDateTime } from "../lib/format";
import { Field } from "./ui";

const IPV4 = /^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$/;
const MAC = /^[0-9a-f]{2}(:[0-9a-f]{2}){5}$/i;
const HOST = /^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$/;

// Settings of a network Hyperlite routes itself (NAT or isolated): subnet, DHCP range, mode. The backend checks the
// subnet against the other networks and the reservations; a subnet or mode change on a running network waits for
// its restart, which is offered here.
function Settings({ name, detail, onChange }) {
  const t = useT();
  const pushToast = useInfraStore((s) => s.pushToast);
  const ipam = detail.ipam;
  const [f, setF] = useState(() => ({
    mode: ipam.mode, adresse: ipam.adresse, masque: ipam.masque, dhcp: Boolean(ipam.dhcp), debut: ipam.dhcp?.debut || "", fin: ipam.dhcp?.fin || "",
  }));
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setF((x) => ({ ...x, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value }));
  const bad = {
    adresse: !IPV4.test(f.adresse), masque: !IPV4.test(f.masque),
    debut: f.dhcp && !IPV4.test(f.debut), fin: f.dhcp && !IPV4.test(f.fin),
  };
  const invalid = Object.values(bad).some(Boolean);
  const dirty = f.mode !== ipam.mode || f.adresse !== ipam.adresse || f.masque !== ipam.masque || f.dhcp !== Boolean(ipam.dhcp)
    || (f.dhcp && (f.debut !== ipam.dhcp?.debut || f.fin !== ipam.dhcp?.fin));

  async function save() {
    setBusy(true);
    try {
      const r = await editNetwork(name, { mode: f.mode, adresse: f.adresse, masque: f.masque, dhcp: f.dhcp ? { debut: f.debut, fin: f.fin } : null });
      pushToast({ kind: "success", title: t("ipam.saved"), message: t(r.a_redemarrer ? "ipam.savedRestart" : "ipam.savedLive") });
      onChange();
    } catch (e) { pushToast({ kind: "error", title: t("ipam.saveFailed"), message: errorMessage(e) }); } finally { setBusy(false); }
  }
  async function restart() {
    if (!(await confirmAction({ title: t("ipam.restartTitle", { name }), message: t("ipam.restartMsg", { n: detail.vms ?? 0 }), confirmLabel: t("ipam.restart"), danger: true }))) return;
    try { await stopNetwork(name); await startNetwork(name); pushToast({ kind: "success", title: t("ipam.restarted"), message: name }); onChange(); }
    catch (e) { pushToast({ kind: "error", title: t("ipam.restartFailed"), message: errorMessage(e) }); onChange(); }
  }
  const input = (k, label, help) => <Field label={label} help={help} error={bad[k] ? t("net.ipInvalid") : null}>{(p) => <input {...p} className="nx-inp nx-mono" value={f[k]} onChange={set(k)} />}</Field>;
  return (
    <section className="nx-stack" aria-label={t("ipam.settings")} style={{ gap: "var(--space-3)" }}>
      <strong>{t("ipam.settings")}</strong>
      {detail.a_redemarrer && (
        <div className="nx-banner nx-banner--info" role="status" style={{ margin: 0 }}>
          {t("ipam.pending")} <button type="button" className="nx-btn" onClick={restart}>{t("ipam.restart")}</button>
        </div>
      )}
      <div className="nx-fg">
        <Field label={t("net.mode")}>{(p) => <select {...p} className="nx-inp" value={f.mode} onChange={set("mode")}><option value="nat">{t("net.mode.nat")}</option><option value="isole">{t("net.mode.isole")}</option></select>}</Field>
        {input("adresse", t("ipam.gateway"))}
        {input("masque", t("ipam.netmask"), t("hlp.netmask"))}
      </div>
      <label className="nx-check"><input type="checkbox" checked={f.dhcp} onChange={set("dhcp")} /> {t("ipam.dhcpOn")}</label>
      {f.dhcp && <div className="nx-fg">{input("debut", t("ipam.dhcpStart"))}{input("fin", t("ipam.dhcpEnd"))}</div>}
      <p className="nx-muted" style={{ margin: 0, fontSize: "var(--fs-13)" }}>{t("ipam.settingsHelp")}</p>
      <div><button type="button" className="nx-btn nx-btn--primary" disabled={!dirty || invalid || busy} onClick={save}>{t("ipam.save")}</button></div>
    </section>
  );
}

// Addresses in use: the DHCP reservations (fixed addresses, including the ones Hyperlite gives a VM at creation)
// and the current leases, which can be turned into a reservation.
function Addresses({ name, detail, isAdmin, onChange }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const pushToast = useInfraStore((s) => s.pushToast);
  const ipam = detail.ipam;
  const [f, setF] = useState({ mac: "", ip: "", nom: "" });
  const [busy, setBusy] = useState(false);
  const reserved = new Set((ipam?.reservations || []).map((r) => r.mac));
  const bad = { mac: f.mac !== "" && !MAC.test(f.mac), ip: f.ip !== "" && !IPV4.test(f.ip), nom: f.nom !== "" && !HOST.test(f.nom) };
  const canAdd = isAdmin && ipam?.modifiable && ipam?.dhcp;

  async function add() {
    setBusy(true);
    try {
      await addNetworkReservation(name, { mac: f.mac.toLowerCase(), ip: f.ip, nom: f.nom || null });
      pushToast({ kind: "success", title: t("ipam.reserved"), message: `${f.ip} → ${f.mac}` });
      setF({ mac: "", ip: "", nom: "" });
      onChange();
    } catch (e) { pushToast({ kind: "error", title: t("ipam.reserveFailed"), message: errorMessage(e) }); } finally { setBusy(false); }
  }
  async function remove(r) {
    if (!(await confirmAction({ title: t("ipam.unreserveTitle", { ip: r.ip }), message: t("ipam.unreserveMsg"), confirmLabel: t("sec.remove"), danger: true }))) return;
    try { await deleteNetworkReservation(name, r.mac); onChange(); }
    catch (e) { pushToast({ kind: "error", title: t("ipam.unreserveFailed"), message: errorMessage(e) }); }
  }
  const leases = detail.baux_dhcp || [];
  return (
    <section className="nx-stack" aria-label={t("ipam.addresses")} style={{ gap: "var(--space-3)" }}>
      <strong>{t("ipam.addresses")}</strong>
      {ipam?.modifiable && (
        <div>
          <span className="nx-muted" style={{ fontSize: "var(--fs-13)" }}>{t("ipam.reservations")}</span>
          {ipam.reservations.length === 0 ? <p className="nx-muted" style={{ margin: "4px 0 0" }}>{t("ipam.noReservations")}</p> : (
            <ul className="nx-iplist">{ipam.reservations.map((r) => (
              <li key={r.mac}><span className="nx-mono">{r.ip}</span><span className="nx-mono nx-muted">{r.mac}</span><span className="nx-muted">{r.nom || ""}</span><span />
                {isAdmin && <button type="button" className="nx-btn nx-btn--ghost" aria-label={t("ipam.unreserveX", { ip: r.ip })} onClick={() => remove(r)}>{t("sec.remove")}</button>}</li>
            ))}</ul>
          )}
        </div>
      )}
      <div>
        <span className="nx-muted" style={{ fontSize: "var(--fs-13)" }}>{t("net.leases")}</span>
        {leases.length === 0 ? <p className="nx-muted" style={{ margin: "4px 0 0" }}>{t("net.noLeases")}</p> : (
          <ul className="nx-iplist">{leases.map((b) => (
            <li key={`${b.mac}-${b.ip}`}><span className="nx-mono">{b.ip}</span><span className="nx-mono nx-muted">{b.mac}</span><span className="nx-muted">{b.hostname || ""}</span>
              <span className="nx-muted">{b.expire ? t("ipam.expires", { d: formatDateTime(b.expire, lang) }) : ""}</span>
              {canAdd && !reserved.has((b.mac || "").toLowerCase()) && <button type="button" className="nx-btn nx-btn--ghost" aria-label={t("ipam.reserveX", { ip: b.ip })} onClick={() => setF({ mac: b.mac || "", ip: b.ip || "", nom: HOST.test(b.hostname || "") ? b.hostname : "" })}>{t("ipam.reserve")}</button>}</li>
          ))}</ul>
        )}
      </div>
      {canAdd && (
        <div className="nx-fg" role="group" aria-label={t("ipam.add")}>
          <Field label={t("ipam.mac")} help={t("hlp.reservation")} error={bad.mac ? t("ipam.macInvalid") : null}>{(p) => <input {...p} className="nx-inp nx-mono" value={f.mac} placeholder="52:54:00:12:34:56" onChange={(e) => setF((x) => ({ ...x, mac: e.target.value.trim() }))} />}</Field>
          <Field label={t("ipam.ip")} error={bad.ip ? t("net.ipInvalid") : null}>{(p) => <input {...p} className="nx-inp nx-mono" value={f.ip} onChange={(e) => setF((x) => ({ ...x, ip: e.target.value.trim() }))} />}</Field>
          <Field label={t("ipam.name")} error={bad.nom ? t("ipam.nameInvalid") : null}>{(p) => <input {...p} className="nx-inp nx-mono" value={f.nom} onChange={(e) => setF((x) => ({ ...x, nom: e.target.value.trim() }))} />}</Field>
          <div style={{ alignSelf: "end" }}><button type="button" className="nx-btn" disabled={!f.mac || !f.ip || bad.mac || bad.ip || bad.nom || busy} onClick={add}>{t("ipam.add")}</button></div>
        </div>
      )}
    </section>
  );
}

export default function NetworkIpam({ name, detail, isAdmin, onChange }) {
  return (
    <>
      {isAdmin && detail.ipam?.modifiable && <Settings key={JSON.stringify(detail.ipam)} name={name} detail={detail} onChange={onChange} />}
      <Addresses name={name} detail={detail} isAdmin={isAdmin} onChange={onChange} />
    </>
  );
}
