import { useEffect, useState } from "react";
import { ChevronRight, Download, File, Folder, FolderUp, Link2 } from "lucide-react";
import { closeBackupFiles, downloadBackupFile, listBackupDir, openBackupFiles } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useT, useLangStore } from "../i18n";
import { errorMessage } from "../lib/errors";
import { formatDateTime, formatSizeMb } from "../lib/format";
import { ErrorState } from "./States";
import { Field, SideDrawer } from "./ui";

const join = (dir, name) => (dir === "/" ? `/${name}` : `${dir}/${name}`);
const parent = (dir) => dir.replace(/\/[^/]+$/, "") || "/";

// Files of one backup, read from its disks without restoring the VM: pick a filesystem, walk its folders, download
// a file, or a folder as a .tar.gz. The backup is opened read-only; the session closes with the drawer.
export default function BackupFileBrowser({ backup, onClose }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const pushToast = useInfraStore((s) => s.pushToast);
  const [session, setSession] = useState(null);
  const [error, setError] = useState(null);
  const [device, setDevice] = useState("");
  const [path, setPath] = useState("/");
  const [entries, setEntries] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    let opened = null;
    openBackupFiles(backup.id)
      .then((s) => { opened = s.session; if (!alive) { closeBackupFiles(s.session).catch(() => {}); return; } setSession(s); setDevice(s.filesystems[0]?.device || ""); })
      .catch((e) => alive && setError(errorMessage(e)));
    return () => { alive = false; if (opened) closeBackupFiles(opened).catch(() => {}); };
  }, [backup.id]);

  useEffect(() => {
    if (!session || !device) return undefined;
    let alive = true;
    setEntries(null);
    listBackupDir(session.session, device, path)
      .then((r) => alive && setEntries(r.entries))
      .catch((e) => { if (alive) { pushToast({ kind: "error", title: t("fr.listFailed"), message: errorMessage(e) }); setEntries([]); } });
    return () => { alive = false; };
    // t is a new function at each render: listing again for it would loop. The folder is what decides.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session, device, path]);

  async function download(name) {
    setBusy(true);
    try { await downloadBackupFile(session.session, device, name ? join(path, name) : path); }
    catch (e) { pushToast({ kind: "error", title: t("fr.downloadFailed"), message: errorMessage(e) }); } finally { setBusy(false); }
  }
  const crumbs = path === "/" ? [] : path.slice(1).split("/");
  const fsLabel = (f) => `${f.device} · ${f.type}${f.label ? ` · ${f.label}` : ""}${f.size ? ` · ${formatSizeMb(f.size / 1048576, lang)}` : ""}`;

  return (
    <SideDrawer open title={t("fr.title", { name: backup.vm_name || "", date: formatDateTime(backup.cree_le, lang) })} onClose={onClose}>
      {error ? <ErrorState message={error} /> : !session ? (
        <p className="nx-muted" role="status" style={{ margin: 0 }}>{t("fr.opening")}</p>
      ) : session.filesystems.length === 0 ? (
        <p className="nx-muted" style={{ margin: 0 }}>{t("fr.noFs")}</p>
      ) : (
        <>
          <p className="nx-muted" style={{ margin: 0, fontSize: "var(--fs-13)" }}>{t("fr.help")}</p>
          <Field label={t("fr.fs")}>{(p) => <select {...p} className="nx-inp" value={device} onChange={(e) => { setDevice(e.target.value); setPath("/"); }}>{session.filesystems.map((f) => <option key={f.device} value={f.device}>{fsLabel(f)}</option>)}</select>}</Field>
          <nav className="nx-crumbs2" aria-label={t("fr.path")}>
            <button type="button" className="nx-lnk nx-mono" onClick={() => setPath("/")}>/</button>
            {crumbs.map((c, i) => (
              <span key={i}><ChevronRight size={12} aria-hidden="true" /><button type="button" className="nx-lnk nx-mono" onClick={() => setPath(`/${crumbs.slice(0, i + 1).join("/")}`)}>{c}</button></span>
            ))}
            {path !== "/" && <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm" disabled={busy} onClick={() => download("")} title={t("fr.downloadDir")}><Download size={14} aria-hidden="true" />{t("fr.downloadHere")}</button>}
          </nav>
          {entries == null ? <p className="nx-muted" role="status" style={{ margin: 0 }}>{t("loading")}</p> : (
            <ul className="nx-files" aria-label={t("fr.entries")}>
              {path !== "/" && <li><button type="button" className="nx-lnk" onClick={() => setPath(parent(path))}><FolderUp size={15} aria-hidden="true" />..</button></li>}
              {entries.length === 0 && <li className="nx-muted">{t("fr.empty")}</li>}
              {entries.map((e) => (
                <li key={e.name}>
                  {e.type === "dir"
                    ? <button type="button" className="nx-lnk" onClick={() => setPath(join(path, e.name))}><Folder size={15} aria-hidden="true" />{e.name}</button>
                    : <span className={e.type === "file" ? undefined : "nx-muted"}>{e.type === "link" ? <Link2 size={15} aria-hidden="true" /> : <File size={15} aria-hidden="true" />}{e.name}</span>}
                  <span className="nx-mono nx-muted">{e.type === "file" ? formatSizeMb(e.size / 1048576, lang) : ""}</span>
                  <span className="nx-mono nx-muted">{e.mtime ? formatDateTime(e.mtime, lang) : ""}</span>
                  {(e.type === "file" || e.type === "dir") && <button type="button" className="nx-btn nx-btn--ghost nx-btn--sm nx-btn--icon" disabled={busy} aria-label={t(e.type === "dir" ? "fr.downloadDirX" : "fr.downloadX", { name: e.name })} onClick={() => download(e.name)}><Download size={14} aria-hidden="true" /></button>}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </SideDrawer>
  );
}
