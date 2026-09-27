import { useEffect, useState } from "react";
import { Monitor } from "lucide-react";
import "./next.css";
import "./refonte.css";
import { useAuthStore } from "../store/useAuthStore";
import { fetchVM } from "../api/client";
import { useT, useLangStore } from "./i18n";
import { useThemeStore } from "./tokens/theme";
import { errorMessage } from "./lib/errors";
import { StatePill } from "./components/ui";
import VmConsole from "./pages/VmConsole";
import NextLogin from "./NextLogin";

// Separate console window of the rebuilt interface (/console/:name, opened from the VM page): the same console as
// the VM page, full height, connected automatically. Same origin, so the same session as the main window.
export default function NextConsoleWindow({ name, initialMode }) {
  const t = useT();
  const lang = useLangStore((s) => s.lang);
  const initTheme = useThemeStore((s) => s.init);
  const status = useAuthStore((s) => s.status);
  const restoreSession = useAuthStore((s) => s.restoreSession);
  const [vm, setVm] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    const root = document.documentElement;
    root.dataset.ui = "next";
    root.lang = lang;
    document.title = `${name} — ${t("tab.console")}`;
    const cleanup = initTheme();
    return () => cleanup?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => { restoreSession(); }, [restoreSession]);
  useEffect(() => {
    if (status !== "authenticated") return undefined;
    const load = () => fetchVM(name).then((v) => { setVm(v); setError(null); }).catch((e) => setError(errorMessage(e)));
    load();
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, [name, status]);

  if (status === "anonymous") return <NextLogin />;
  return (
    <div className="nx-root nx-console-window">
      <header className="nx-console-head">
        <Monitor size={17} aria-hidden="true" />
        <h1>{name}</h1>
        {vm && <StatePill kind="vm" wire={vm.etat} />}
      </header>
      {error && <p className="nx-f-h is-error" role="alert">{error}</p>}
      {vm ? <VmConsole resource={vm} standalone initialMode={initialMode} /> : !error && <p className="nx-muted" role="status">{t("loading")}</p>}
    </div>
  );
}
