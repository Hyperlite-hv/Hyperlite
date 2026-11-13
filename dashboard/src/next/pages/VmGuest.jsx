import { useEffect, useState } from "react";
import { Cpu, Play, RefreshCw } from "lucide-react";
import { fetchVmGuest, runInVmGuest } from "../../api/client";
import { useAuthStore } from "../../store/useAuthStore";
import { useT, useLangStore } from "../i18n";
import { capabilities } from "../lib/capabilities";
import { errorMessage } from "../lib/errors";
import { formatSizeMb } from "../lib/format";
import { Empty, Field, Meter, TableWrap } from "../components/ui";
import { KeyValues } from "../components/portal";
import { InlineError } from "../components/States";

// Ready-made scripts, as a starting point (Linux shells, then Windows ones).
const PRESETS = [
  { id: "sysinfo", shell: "sh", script: "uname -a\nuptime\ncat /etc/os-release 2>/dev/null | head -4\ndf -h -x tmpfs -x devtmpfs" },
  { id: "top", shell: "sh", script: "ps aux --sort=-%cpu | head -15" },
  { id: "updates", shell: "sh", script: "(apt list --upgradable 2>/dev/null || dnf -q check-update 2>/dev/null || true) | head -50" },
  { id: "net", shell: "sh", script: "ip -brief address\nip route\ncat /etc/resolv.conf" },
  { id: "services", shell: "sh", script: "systemctl --failed --no-pager" },
  { id: "winfo", shell: "powershell", script: "Get-ComputerInfo -Property OsName,OsVersion,CsName,OsLastBootUpTime | Format-List" },
  { id: "wnet", shell: "cmd", script: "ipconfig /all" },
];
const SHELLS = ["sh", "bash", "powershell", "cmd"];

const usable = (vm) => vm.state === "active" && vm.guest_agent === "active";

function NotReady({ vm }) {
  const t = useT();
  return <Empty icon={Cpu} title={vm.state !== "active" ? t("gst.stopped") : t("gst.noAgent")} text={vm.state !== "active" ? t("gst.stoppedHelp") : t("gst.noAgentHelp")} />;
}

// VM ▸ Guest system: what the guest OS says about itself through the agent.
export function VmGuestInfoPage({ resource: vm }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const [info, setInfo] = useState(null);
  const [error, setError] = useState(null);
  const ready = usable(vm);
  const load = () => {
    setError(null);
    fetchVmGuest(vm.name, vm.node).then(setInfo).catch((e) => { setInfo(null); setError(errorMessage(e)); });
  };
  useEffect(() => { if (ready) load(); }, [vm.name, vm.node, ready]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!ready) return <NotReady vm={vm} />;
  if (error) return <InlineError message={error} onRetry={load} />;
  if (!info) return <p className="nx-muted">{t("loading")}</p>;
  const os = info.os || {};
  const rows = [
    [t("gst.os"), os["pretty-name"] || [os.name, os.version].filter(Boolean).join(" ") || "—"],
    [t("gst.kernel"), os["kernel-release"] || "—"],
    [t("gst.arch"), os.machine || "—"],
    [t("gst.hostname"), info.hostname || "—"],
    [t("gst.timezone"), info.timezone || "—"],
    [t("gst.users"), info.users?.length ? info.users.join(", ") : t("gst.noUser")],
    [t("gst.agent"), info.agent_version || "—"],
  ];
  return (
    <>
      <section className="hc-gst">
        <h2>{t("gst.system")}<button type="button" className="nx-lnk" onClick={load}><RefreshCw size={13} aria-hidden="true" />{t("gst.reread")}</button></h2>
        <KeyValues rows={rows} />
      </section>
      <section className="hc-gst">
        <h2>{t("gst.filesystems")}</h2>
        {!info.filesystems?.length ? <p className="nx-muted">{t("gst.noFs")}</p> : (
          <TableWrap>
            <table className="nx-table hc-grid--list">
              <thead><tr><th scope="col">{t("gst.mount")}</th><th scope="col">{t("stor.type")}</th><th scope="col">{t("stor.usage")}</th><th scope="col" className="nx-num">{t("gst.used")}</th><th scope="col" className="nx-num">{t("stor.capacity")}</th></tr></thead>
              <tbody>{info.filesystems.map((f) => {
                const pct = f.total_bytes > 0 ? (f.used_bytes / f.total_bytes) * 100 : null;
                return (
                  <tr key={f.mountpoint}>
                    <th scope="row" className="nx-mono">{f.mountpoint}</th><td>{f.type}</td>
                    <td>{pct == null ? "—" : <Meter value={pct} label={`${f.mountpoint} ${t("stor.usage")}`} />}</td>
                    <td className="nx-num nx-mono">{f.used_bytes != null ? formatSizeMb(f.used_bytes / 1048576, lang) : "—"}</td>
                    <td className="nx-num nx-mono">{f.total_bytes != null ? formatSizeMb(f.total_bytes / 1048576, lang) : "—"}</td>
                  </tr>
                );
              })}</tbody>
            </table>
          </TableWrap>
        )}
      </section>
    </>
  );
}

// VM ▸ Run command: a script run in the guest's shell through the agent, its output and exit code.
export function VmRunCommandPage({ resource: vm }) {
  const t = useT();
  const caps = capabilities(useAuthStore((s) => s.role));
  const [shell, setShell] = useState("sh");
  const [script, setScript] = useState("");
  const [timeoutS, setTimeoutS] = useState(30);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  if (!usable(vm)) return <NotReady vm={vm} />;
  const preset = (id) => { const p = PRESETS.find((x) => x.id === id); if (p) { setShell(p.shell); setScript(p.script); } };
  async function run() {
    if (!script.trim() || busy) return;
    setBusy(true); setError(null); setResult(null);
    const started = Date.now();
    try { setResult({ ...(await runInVmGuest(vm.name, vm.node, { shell, script, timeoutS })), at: started, shell }); }
    catch (e) { setError(errorMessage(e)); }
    finally { setBusy(false); }
  }
  return (
    <>
      <p className="nx-ph-desc">{t("run.desc")}</p>
      {!caps.admin && <p className="nx-hint">{t("run.needsPrivilege")}</p>}
      <div className="hc-run">
        <div className="hc-run-opts">
          <Field label={t("run.preset")}>{(p) => (
            <select {...p} className="nx-inp" value="" onChange={(e) => preset(e.target.value)}>
              <option value="">{t("run.pickPreset")}</option>
              {PRESETS.map((x) => <option key={x.id} value={x.id}>{t(`run.p.${x.id}`)}</option>)}
            </select>
          )}</Field>
          <Field label={t("run.shell")}>{(p) => <select {...p} className="nx-inp" value={shell} onChange={(e) => setShell(e.target.value)}>{SHELLS.map((s) => <option key={s} value={s}>{t(`run.sh.${s}`)}</option>)}</select>}</Field>
          <Field label={t("run.timeout")} unit="s">{(p) => <input {...p} className="nx-inp" type="number" min={1} max={120} value={timeoutS} onChange={(e) => setTimeoutS(Math.max(1, Math.min(120, Number(e.target.value) || 30)))} />}</Field>
        </div>
        <Field label={t("run.script")} hint={t("run.scriptHelp")}>{(p) => (
          <textarea {...p} className="nx-inp nx-mono hc-run-script" rows={8} value={script} spellCheck={false} autoComplete="off"
            onChange={(e) => setScript(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); run(); } }} placeholder={shell === "powershell" || shell === "cmd" ? "Get-Service | Where Status -eq Running" : "df -h"} />
        )}</Field>
        <div><button type="button" className="nx-btn nx-btn--primary" onClick={run} aria-disabled={!script.trim() || busy || undefined}><Play size={16} aria-hidden="true" />{busy ? t("run.running") : t("run.run")}</button></div>
      </div>
      {error && <InlineError message={error} />}
      {result && (
        <section className="hc-run-out" aria-live="polite">
          <h2>{t("run.output")}</h2>
          <p className="hc-run-meta">
            {result.timed_out ? <span className="hc-run-code" data-state="timeout">{t("run.timedOut", { s: timeoutS })}</span>
              : <span className="hc-run-code" data-state={result.exit_code === 0 ? "ok" : "error"}>{t("run.exit", { code: result.exit_code })}</span>}
            <span className="nx-muted">{t("run.took", { s: result.duration_s })}</span>
            {result.truncated && <span className="nx-muted">{t("run.truncated")}</span>}
          </p>
          {result.stdout && <><h3>{t("run.stdout")}</h3><pre className="hc-run-pre" tabIndex={0}>{result.stdout}</pre></>}
          {result.stderr && <><h3>{t("run.stderr")}</h3><pre className="hc-run-pre hc-run-pre--err" tabIndex={0}>{result.stderr}</pre></>}
          {!result.stdout && !result.stderr && !result.timed_out && <p className="nx-muted">{t("run.noOutput")}</p>}
        </section>
      )}
      <p className="nx-hint">{t("run.audit")}</p>
    </>
  );
}
