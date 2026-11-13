import { useCallback, useEffect, useRef, useState } from "react";
import { Download, Monitor, RefreshCw, ShieldCheck } from "lucide-react";
import { downloadGalleryImage, fetchImageGallery } from "../../api/client";
import { useInfraStore } from "../../store/useInfraStore";
import { useAuthStore } from "../../store/useAuthStore";
import { useT, useLangStore } from "../i18n";
import { capabilities } from "../lib/capabilities";
import { errorMessage } from "../lib/errors";
import { formatDateTime, formatSizeMb } from "../lib/format";
import { requestIntent } from "../lib/intents";
import { Loading } from "./ui";
import { ErrorState } from "./States";

const asList = (r) => (Array.isArray(r) ? r : Array.isArray(r?.items) ? r.items : []);

// Library ▸ Gallery: official cloud images, checked against their distribution's checksums when downloaded, then a
// VM created from one like from any imported disk.
export default function ImageGallery() {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const caps = capabilities(useAuthStore((s) => s.role));
  const pushToast = useInfraStore((s) => s.pushToast);
  const tasks = useInfraStore((s) => s.tasks);
  const [images, setImages] = useState(null);
  const [error, setError] = useState(null);
  const load = useCallback(() => {
    fetchImageGallery().then((r) => { setImages(asList(r)); setError(null); }).catch((e) => setError(errorMessage(e)));
  }, []);
  useEffect(() => { load(); }, [load]);
  // A download that ends changes what is there: reload when the running ones go down.
  const running = tasks.filter((x) => x.type === "gallery_download" && x.status === "running");
  const prev = useRef(running.length);
  useEffect(() => { if (running.length < prev.current) load(); prev.current = running.length; }, [running.length, load]);
  const progressOf = (img) => running.find((x) => x.target === img.file);

  async function download(img) {
    try {
      await downloadGalleryImage(img.id);
      pushToast({ kind: "info", title: t("gal.started"), message: img.name });
    } catch (e) { pushToast({ kind: "error", title: t("gal.failed"), message: errorMessage(e) }); }
  }
  const createVm = (img) => { requestIntent("vm-from-image", img.file); window.dispatchEvent(new CustomEvent("nx:wizard", { detail: "vm" })); };

  if (error) return <ErrorState message={error} onRetry={load} />;
  if (!images) return <Loading />;
  return (
    <>
      <p className="nx-muted">{t("gal.desc")}</p>
      <ul className="hc-gal">
        {images.map((img) => {
          const task = progressOf(img);
          return (
            <li key={img.id} className="hc-gal-c">
              <div className="hc-gal-h"><span className="hc-gal-fam" data-family={img.family} aria-hidden="true">{img.family.slice(0, 2).toUpperCase()}</span><div><b>{img.name}</b><small>{t("gal.cloud")}</small></div></div>
              <p className="hc-gal-m"><ShieldCheck size={14} aria-hidden="true" />{t("gal.checked", { algo: img.algo.toUpperCase() })}</p>
              {img.downloaded
                ? <p className="hc-gal-m">{t("gal.have", { size: formatSizeMb(img.size / 1048576, lang), date: formatDateTime(img.downloaded_at, lang) })}</p>
                : <p className="hc-gal-m nx-muted">{t("gal.notYet")}</p>}
              {task && <span className="nx-tray-bar" role="progressbar" aria-label={t("gal.downloading", { name: img.name })} aria-valuemin={0} aria-valuemax={100} aria-valuenow={task.progress || 0}><span style={{ width: `${task.progress || 0}%` }} /></span>}
              <div className="hc-gal-a">
                {img.downloaded && caps.create && <button type="button" className="nx-btn nx-btn--primary nx-btn--sm" onClick={() => createVm(img)}><Monitor size={14} aria-hidden="true" />{t("gal.createVm")}</button>}
                {caps.admin && <button type="button" className="nx-btn nx-btn--sm" onClick={() => download(img)} aria-disabled={!!task || undefined}>
                  {img.downloaded ? <RefreshCw size={14} aria-hidden="true" /> : <Download size={14} aria-hidden="true" />}{task ? t("gal.downloadingShort") : img.downloaded ? t("gal.update") : t("gal.download")}
                </button>}
              </div>
            </li>
          );
        })}
      </ul>
    </>
  );
}
