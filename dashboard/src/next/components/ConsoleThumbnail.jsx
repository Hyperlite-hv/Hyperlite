import { useEffect, useRef, useState } from "react";
import { MonitorPlay } from "lucide-react";
import { fetchVmScreenshot } from "../../api/client";
import { useT } from "../i18n";

const REFRESH_MS = 30_000;

// A running VM's screen, refreshed every 30 seconds while the page is shown; a click opens the console. Shows nothing
// when the screen cannot be read (no display, no right to the console).
export default function ConsoleThumbnail({ vm, onOpen }) {
  const t = useT();
  const [url, setUrl] = useState(null);
  const [at, setAt] = useState(null);
  const current = useRef(null);
  const running = vm.state === "active";
  useEffect(() => {
    if (!running) return undefined;
    let alive = true;
    const load = async () => {
      if (document.hidden) return;
      const blob = await fetchVmScreenshot(vm.name, vm.node, 480).catch(() => null);
      if (!alive) return;
      const next = blob ? URL.createObjectURL(blob) : null;
      if (current.current) URL.revokeObjectURL(current.current);
      current.current = next;
      setUrl(next);
      setAt(new Date());
    };
    load();
    const timer = setInterval(load, REFRESH_MS);
    return () => { alive = false; clearInterval(timer); if (current.current) URL.revokeObjectURL(current.current); current.current = null; };
  }, [vm.name, vm.node, running]);
  if (!running || !url) return null;
  return (
    <figure className="hc-thumb">
      <button type="button" onClick={onOpen} aria-label={t("thumb.open", { name: vm.name })}>
        <img src={url} alt={t("thumb.alt", { name: vm.name })} />
        <span className="hc-thumb-go"><MonitorPlay size={14} aria-hidden="true" />{t("thumb.console")}</span>
      </button>
      <figcaption>{t("thumb.at", { time: at?.toLocaleTimeString() })}</figcaption>
    </figure>
  );
}
