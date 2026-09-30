import { useEffect, useState } from "react";
import { createContainer, fetchImageEnv, searchDockerHub } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { confirmAction } from "../../store/useConfirmStore";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "@/components/ui/dialog";
import { useT } from "../i18n";
import { errorMessage } from "../lib/errors";
import { GALLERY, NAME_RE } from "../lib/containerImages";

const initial = (networks) => ({ storage: "", kind: "docker", name: "", vcpu: 1, memory_mb: 512, username: "", password: "", network: networks[0]?.nom || "default", image: "", command: "", env: "" });
// A Docker container gets its address from Hyperlite on the network's own subnet: NAT and isolated networks only.
const hasSubnet = (n) => n.type === "nat" || n.type === "isole";
const ENV_LINE = /^[A-Za-z_][A-Za-z0-9_]*=/;

// Splits a command line like a shell would for plain arguments: spaces separate, quotes group, \ escapes.
export function splitArgs(line) {
  const out = [];
  let cur = "", quote = null, started = false;
  for (let i = 0; i < line.length; i += 1) {
    const c = line[i];
    if (quote) {
      if (c === quote) quote = null;
      else if (c === "\\" && quote === '"' && i + 1 < line.length) cur += line[(i += 1)];
      else cur += c;
    } else if (c === "'" || c === '"') { quote = c; started = true; }
    else if (c === "\\" && i + 1 < line.length) { cur += line[(i += 1)]; started = true; }
    else if (/\s/.test(c)) { if (started) { out.push(cur); cur = ""; started = false; } }
    else { cur += c; started = true; }
  }
  if (quote) return null;
  if (started) out.push(cur);
  return out;
}
// A password for a variable the image needs (POSTGRES_PASSWORD...): letters and digits only, so that no image's
// entrypoint script or connection string has to quote it.
export function generatePassword(length = 20) {
  const chars = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789";
  const bytes = new Uint8Array(length);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => chars[b % chars.length]).join("");
}
const parseEnv = (text) => Object.fromEntries(text.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => [l.slice(0, l.indexOf("=")), l.slice(l.indexOf("=") + 1)]));
const int = (v, min, max) => v !== "" && Number.isInteger(Number(v)) && Number(v) >= min && Number(v) <= max;

// One screen on purpose: a container has far fewer choices than a VM (no ISO, no OS to pick). Same payload as
// the historical dialog. Errors are shown next to their field on submit, a refusal keeps every typed value,
// one submit only, and closing with typed values asks first.
export default function CreateContainerWizard({ open, onClose, triggerRef }) {
  const t = useT();
  const networks = useInfraStore((s) => s.networks);
  // Local directory pools only: a container filesystem needs root ownership and device files (see the API).
  const dirPools = useInfraStore((s) => s.storagePools).filter((p) => p.type === "dir" && p.etat === "actif");
  const containersTaken = useInfraStore((s) => s.vms); // VM names share the libvirt namespace
  const addTask = useInfraStore((s) => s.addTask);
  const completeTask = useInfraStore((s) => s.completeTask);
  const pushToast = useInfraStore((s) => s.pushToast);
  const [form, setForm] = useState(() => ({ ...initial(networks), image: "nginx:latest" }));
  const [attempted, setAttempted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  // What the chosen image needs (GET /containers/image-env) and the values typed or generated for it.
  const [needs, setNeeds] = useState({ requises: [], utiles: [] });
  const [req, setReq] = useState({});

  useEffect(() => {
    setForm((f) => {
      const up = networks.filter((n) => n.actif !== false);
      const pool = f.kind === "docker" ? up.filter(hasSubnet) : up;
      return pool.some((n) => n.nom === f.network) ? f : { ...f, network: pool[0]?.nom || f.network };
    });
  }, [networks, form.kind]);
  useEffect(() => {
    if (!query.trim()) return undefined;
    const id = setTimeout(() => { searchDockerHub(query).then((r) => setResults(Array.isArray(r) ? r : [])).catch(() => setResults([])); }, 400);
    return () => clearTimeout(id);
  }, [query]);

  useEffect(() => {
    if (form.kind !== "docker" || !form.image.trim()) { setNeeds({ requises: [], utiles: [] }); return undefined; }
    let alive = true;
    const id = setTimeout(() => {
      fetchImageEnv(form.image.trim()).then((r) => {
        if (!alive || !r) return;
        setNeeds({ requises: r.requises || [], utiles: r.utiles || [] });
        // A password the image needs is generated at once; it stays visible so it can be noted, and can be changed.
        setReq((cur) => Object.fromEntries((r.requises || []).map((g) => g[0]).map((v) => [v.nom, cur[v.nom] ?? (v.generer ? generatePassword() : "")])));
      }).catch(() => alive && setNeeds({ requises: [], utiles: [] }));
    }, 300);
    return () => { alive = false; clearTimeout(id); };
  }, [form.image, form.kind]);

  const patch = (f) => setForm((x) => ({ ...x, ...f }));
  const pick = (image) => { patch({ image }); setQuery(""); setResults([]); };
  const taken = containersTaken.some((v) => v.nom === form.name);
  const docker = form.kind === "docker";
  // Started networks only (a stopped one is refused by libvirt at start), and for Docker those with their own subnet.
  const started = networks.filter((n) => n.actif !== false);
  const usableNetworks = docker ? started.filter(hasSubnet) : started;
  const envLines = form.env.split("\n").map((l) => l.trim()).filter(Boolean);
  const args = form.command.trim() ? splitArgs(form.command.trim()) : [];
  const typedEnv = envLines.every((l) => ENV_LINE.test(l)) ? parseEnv(form.env) : {};
  // A requirement is met by its field, or by one of its variables typed in the environment box (which then wins).
  const inBox = (group) => group.some((v) => (typedEnv[v.nom] ?? "").trim() !== "");
  const unmet = docker ? needs.requises.filter((g) => !inBox(g) && !(req[g[0].nom] ?? "").trim()) : [];
  const errors = {
    name: !NAME_RE.test(form.name) ? "wz.e.name" : taken ? "wz.e.taken" : "",
    vcpu: int(form.vcpu, 1, 16) ? "" : "cw.e.vcpu",
    memory_mb: int(form.memory_mb, 128, 1048576) ? "" : "cw.e.memory",
    username: docker || /^[a-zA-Z_][a-zA-Z0-9_-]{0,31}$/.test(form.username) ? "" : "wz.e.user",
    password: docker || form.password.length >= 4 ? "" : "wz.e.password",
    network: usableNetworks.some((n) => n.nom === form.network) || (networks.length === 0 && !docker) ? "" : docker ? "cw.e.dockerNetwork" : "wz.e.network",
    image: docker && !form.image.trim() ? "cw.e.dockerImage" : "",
    command: docker && args === null ? "cw.e.command" : "",
    env: docker && envLines.some((l) => !ENV_LINE.test(l)) ? "cw.e.env" : "",
    required: unmet.length ? "cw.e.required" : "",
  };
  const bad = Object.values(errors).some(Boolean);
  const dirty = Boolean(form.name || form.username || form.password || form.command || form.env);
  const fe = (k) => attempted && errors[k] && <span className="nx-hint nx-hint--error" id={`cw-${k}`}>{t(errors[k])}</span>;
  const inv = (k) => ({ "aria-invalid": attempted && errors[k] ? true : undefined, "aria-describedby": attempted && errors[k] ? `cw-${k}` : undefined });

  const reset = () => { setForm({ ...initial(networks), image: "nginx:latest" }); setAttempted(false); setError(null); setQuery(""); setResults([]); setReq({}); };
  async function requestClose() {
    if (busy) return;
    if (dirty && !(await confirmAction({ title: t("wz.discardTitle"), message: t("wz.discardMsg"), confirmLabel: t("wz.discard") }))) return;
    onClose(); reset();
  }
  function dockerEnv() {
    const fromFields = Object.fromEntries(needs.requises.filter((g) => !inBox(g)).map((g) => [g[0].nom, (req[g[0].nom] ?? "").trim()]).filter(([, v]) => v));
    const env = { ...fromFields, ...(envLines.length ? parseEnv(form.env) : {}) };
    return Object.keys(env).length ? env : null;
  }
  const addUseful = (name) => patch({ env: `${form.env.replace(/\s*$/, "")}${form.env.trim() ? "\n" : ""}${name}=` });
  const describe = (v) => (t(`envv.${v.nom}`) !== `envv.${v.nom}` ? t(`envv.${v.nom}`) : v.description);
  async function create(e) {
    e.preventDefault();
    if (busy) return;
    if (bad) { setAttempted(true); return; }
    setBusy(true); setError(null);
    const taskId = addTask({ type: "create_container", cible: form.name });
    try {
      const common = { name: form.name, vcpu: Number(form.vcpu), memory_mb: Number(form.memory_mb), network: form.network, image: form.image.trim() || null, storage_pool: form.storage || null };
      const created = await createContainer(docker
        ? { ...common, mode: "application", command: args.length ? args : null, env: dockerEnv() }
        : { ...common, mode: "systeme", username: form.username, password: form.password });
      completeTask(taskId, "termine");
      pushToast({ kind: "success", title: t("ct.created"), message: docker ? t("ct.dockerRunning", { name: form.name, ip: created?.ip || "—" }) : `${form.name}: ${t("ct.building")}` });
      window.dispatchEvent(new Event("nx:containers-changed"));
      onClose(); reset();
    } catch (er) { completeTask(taskId, "echec", errorMessage(er)); setError(errorMessage(er)); }
    finally { setBusy(false); }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) requestClose(); }}>
      <DialogContent className="nx-wizard nx-wizard2 nx-wizard2--sm w-full max-w-3xl sm:max-w-3xl p-0 gap-0 overflow-hidden" onCloseAutoFocus={(e) => { if (triggerRef?.current) { e.preventDefault(); triggerRef.current.focus(); } }}>
        <DialogHeader className="nx-wiz-head">
          <DialogTitle>{t("cw.title")}</DialogTitle>
          <DialogDescription className="nx-muted">{t("cw.desc")}</DialogDescription>
        </DialogHeader>
        <form onSubmit={create} noValidate>
          <div className="nx-wiz-body nx-form">
            <label>{t("ct.name")}<input className="nx-input" aria-label={t("a11y.name")} autoFocus value={form.name} onChange={(e) => patch({ name: e.target.value })} {...inv("name")} />{fe("name")}</label>
            <fieldset className="nx-fieldset">
              <legend>{t("cw.kind")}</legend>
              <div className="nx-tiles">
                <button type="button" className="nx-tile" aria-pressed={docker} onClick={() => patch({ kind: "docker", image: form.image || "nginx:latest" })}><b>{t("cw.kindDocker")}</b><small>{t("cw.kindDockerSub")}</small></button>
                <button type="button" className="nx-tile" aria-pressed={!docker} onClick={() => patch({ kind: "lxc" })}><b>{t("cw.kindLxc")}</b><small>{t("cw.kindLxcSub")}</small></button>
              </div>
            </fieldset>
            <fieldset className="nx-fieldset">
              <legend>{t("ct.image")}</legend>
              <div className="nx-tiles">
                {GALLERY.filter(([key]) => !docker || key).map(([key, label, desc]) => <button type="button" key={label} className="nx-tile" aria-pressed={form.image === key} onClick={() => pick(key)}><b>{label}</b><small>{t(desc)}</small></button>)}
              </div>
              <label>{t("ct.otherImage")}<input className="nx-input" aria-label={t("a11y.docker_hub_image")} value={query} placeholder="traefik, ghcr.io/foo/bar:tag" onChange={(e) => { setQuery(e.target.value); patch({ image: e.target.value }); }} /></label>
              {results.length > 0 && (
                <ul className="nx-list nx-list--vols" aria-label={t("ct.results")}>
                  {results.map((r) => <li key={r.nom}><button type="button" className="nx-link" onClick={() => pick(`${r.nom}:latest`)}>{r.nom}</button><span className="nx-muted">{r.officielle ? `${t("ct.official")} · ` : ""}{r.description}</span><span className="nx-mono nx-muted">★ {r.etoiles}</span></li>)}
                </ul>
              )}
              {form.image && !GALLERY.some((g) => g[0] === form.image) && <span className="nx-hint">{t("ct.selected")} <span className="nx-mono">{form.image}</span></span>}
              {fe("image")}
              <span className="nx-hint">{t(docker ? "cw.imageHelpDocker" : "cw.imageHelp")}</span>
            </fieldset>
            <div className="nx-formgrid nx-fg">
              <label>vCPU<input className="nx-input" aria-label={t("a11y.vcpu")} type="number" min={1} max={16} value={form.vcpu} onChange={(e) => patch({ vcpu: e.target.value })} {...inv("vcpu")} />{fe("vcpu")}</label>
              <label>{t("ct.ram")}<input className="nx-input" aria-label={t("a11y.ram_mb")} type="number" min={128} step={128} value={form.memory_mb} onChange={(e) => patch({ memory_mb: e.target.value })} {...inv("memory_mb")} />{fe("memory_mb")}</label>
              <label>{t("cw.storage")}<select className="nx-input" value={form.storage} onChange={(e) => patch({ storage: e.target.value })}>
                <option value="">{t("cw.storageDefault")}</option>
                {dirPools.map((p) => <option key={p.nom} value={p.nom}>{p.nom}{p.chemin ? ` · ${p.chemin}` : ""}</option>)}
              </select><span className="nx-hint">{t("cw.storageHelp")}</span></label>
              <label>{t("ct.network")}<select className="nx-input" aria-label={t("a11y.network")} value={form.network} onChange={(e) => patch({ network: e.target.value })}>{usableNetworks.length === 0 && <option value={docker ? "" : "default"}>{docker ? t("cw.noDockerNetwork") : "default"}</option>}{usableNetworks.map((n) => <option key={n.nom} value={n.nom}>{n.nom}</option>)}</select>{fe("network")}</label>
            </div>
            {docker ? (<>
              <label>{t("cw.command")}<input className="nx-input nx-mono" autoComplete="off" spellCheck={false} value={form.command} placeholder={t("cw.commandPh")} onChange={(e) => patch({ command: e.target.value })} {...inv("command")} />{fe("command")}<span className="nx-hint">{t("cw.commandHelp")}</span></label>
              {needs.requises.length > 0 && (
                <fieldset className="nx-fieldset">
                  <legend>{t("cw.required")}</legend>
                  <span className="nx-hint">{t("cw.requiredHelp", { image: form.image.trim() })}</span>
                  {needs.requises.map((group) => {
                    const v = group[0];
                    const others = group.slice(1).map((o) => o.nom).join(", ");
                    return inBox(group)
                      ? <span key={v.nom} className="nx-hint">{t("cw.requiredInBox", { name: v.nom })}</span>
                      : (
                        <label key={v.nom}><span className="nx-mono">{v.nom}</span>
                          <span style={{ display: "flex", gap: "var(--space-2)" }}>
                            <input className="nx-input nx-mono" autoComplete="off" spellCheck={false} value={req[v.nom] ?? ""} onChange={(e) => setReq((r) => ({ ...r, [v.nom]: e.target.value }))} aria-invalid={attempted && unmet.includes(group) ? true : undefined} aria-describedby={`cw-req-${v.nom}`} style={{ flex: 1 }} />
                            {v.secret && <button type="button" className="nx-btn nx-btn--ghost" onClick={() => setReq((r) => ({ ...r, [v.nom]: generatePassword() }))}>{t("cw.generate")}</button>}
                          </span>
                          <span className="nx-hint" id={`cw-req-${v.nom}`}>{describe(v)}{v.secret ? ` ${t("cw.noteIt")}` : ""}{others ? ` ${t("cw.orInBox", { names: others })}` : ""}</span>
                          {attempted && unmet.includes(group) && <span className="nx-hint nx-hint--error">{t("cw.e.required")}</span>}
                        </label>
                      );
                  })}
                </fieldset>
              )}
              <label>{t("cw.env")}<textarea className="nx-input nx-mono" rows={3} spellCheck={false} value={form.env} placeholder={"TZ=Europe/Paris"} onChange={(e) => patch({ env: e.target.value })} {...inv("env")} />{fe("env")}<span className="nx-hint">{t("cw.envHelp")}</span>
                {needs.utiles.length > 0 && <span className="nx-hint">{t("cw.useful")}{" "}{needs.utiles.map((v) => <button key={v.nom} type="button" className="nx-link nx-mono" title={describe(v)} onClick={() => addUseful(v.nom)} style={{ marginInlineEnd: "var(--space-2)" }}>+{v.nom}</button>)}</span>}
              </label>
            </>) : (
            <div className="nx-formgrid nx-fg">
              <label>{t("ct.user")}<input className="nx-input" aria-label={t("a11y.user")} autoComplete="off" value={form.username} onChange={(e) => patch({ username: e.target.value })} {...inv("username")} />{fe("username")}</label>
              <label>{t("ct.password")}<input className="nx-input" aria-label={t("a11y.password")} type="password" autoComplete="new-password" value={form.password} onChange={(e) => patch({ password: e.target.value })} {...inv("password")} />{fe("password")}</label>
            </div>
            )}
            <div className="nx-bn" data-tone="info" role="note"><span className="nx-bn-t">{t(docker ? "cw.dockerNote" : "cw.firstBuild")}</span></div>
          </div>
          {error && <div role="alert" className="nx-error nx-wiz-error"><strong>{t("cw.failed")}</strong> <span className="nx-mono" style={{ overflowWrap: "anywhere" }}>{error}</span></div>}
          <DialogFooter className="nx-wiz-foot">
            <span className="nx-sp" />
            <button type="button" className="nx-btn nx-btn--ghost" onClick={requestClose}>{t("action.cancel")}</button>
            <button type="submit" className="nx-btn nx-btn--primary" disabled={busy}>{busy ? t("stor.creating") : t("cw.create")}</button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
